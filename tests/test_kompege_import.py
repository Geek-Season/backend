"""Импорт kompege.ru через наш API: картинки и файлы загружаются, повторный запуск дублей не создаёт."""

import base64
import json

import pytest

from scripts.kompege.import_ import Importer

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
IMG = "data:image/png;base64," + base64.b64encode(PNG).decode()

ITEMS = {
    1: [{"taskId": 101, "number": 1, "comment": "Демоверсия 2027", "difficulty": 1, "key": "26", "video": "",
         "text": f'<p>Граф:</p><p><img src="{IMG}" /></p><p>Найдите длину.</p>', "files": []}],
    27: [{"taskId": 127, "number": 27, "comment": "", "difficulty": 0, "key": "1 2\n3 4", "video": "",
          "text": "<p>Кластеры</p>", "files": [{"url": "/files/a.txt", "name": "27_A.txt"}]}],
    19: [{"taskId": 119, "number": 19, "comment": "", "key": "29", "video": "",
          "text": "<p>Игра. Известно, что Ваня выиграл.</p>",
          "subTask": [{"number": 20, "key": "28 48", "text": "<p>Вопрос 20</p>"},
                      {"number": 21, "key": "44", "text": f'<p>Вопрос 21</p><p><img src="{IMG}" /></p>'}]}],
}


@pytest.fixture
def data_dir(tmp_path):
    (tmp_path / "raw").mkdir()
    for number, items in ITEMS.items():
        (tmp_path / "raw" / f"{number}.json").write_text(json.dumps(items), encoding="utf-8")
    (tmp_path / "files" / "127").mkdir(parents=True)
    (tmp_path / "files" / "127" / "27_A.txt").write_text("1 2\n3 4\n")
    return tmp_path


def run(admin_client, data_dir) -> Importer:
    importer = Importer("", "", data_dir, dry_run=False, client=admin_client)
    importer.run([1, 19, 27], None)
    return importer


def test_import_creates_tasks_with_files(admin_client, client, data_dir, upload_dir):
    report = run(admin_client, data_dir).report
    assert len(report["создано"]) == 3 and not report["ошибки"], report

    task = admin_client.get("/tasks/by-external/kompege:101").json()
    assert task["source"] == "demo" and task["source_label"] == "КЕГЭ (kompege.ru) · Демоверсия 2027"
    assert task["difficulty"] == 60
    assert len(task["images"]) == 1
    assert task["statement"] == f"Граф:\n\n![]({task['images'][0]['url']})\n\nНайдите длину."
    assert admin_client.get(task["images"][0]["url"]).content == PNG

    task = admin_client.get("/tasks/by-external/kompege:127").json()
    assert task["correct_answer"] == "1 2 3 4"
    assert [a["name"] for a in task["attachments"]] == ["27_A.txt"]
    assert client.post(f"/tasks/{task['id']}/check", json={"answer": "1 2 3 4"}).json()["is_correct"]

    task = admin_client.get("/tasks/by-external/kompege:119").json()
    assert task["exam_number"] == "19-21"
    assert [p["correct_answer"] for p in task["parts"]] == ["29", "28 48", "44"]
    assert task["parts"][2]["question"].endswith(f"![]({task['images'][0]['url']})")


def test_second_run_creates_nothing(admin_client, data_dir, upload_dir):
    run(admin_client, data_dir)
    report = run(admin_client, data_dir).report
    assert report["создано"] == [] and len(report["уже есть"]) == 3 and not report["ошибки"]


def test_interrupted_import_is_repaired(admin_client, data_dir, upload_dir):
    # Как будто прошлый запуск упал после POST /tasks: задача есть, но картинка не загружена.
    payload = {"exam_number": "1", "statement": "Граф:\n\n![](pending:0)", "correct_answer": "26",
               "external_id": "kompege:101"}
    task_id = admin_client.post("/tasks", json=payload).json()["id"]

    report = run(admin_client, data_dir).report
    assert report["дозагружено"] == [f"kompege:101: задача {task_id}"]
    task = admin_client.get(f"/tasks/{task_id}/admin").json()
    assert "pending:" not in task["statement"] and len(task["images"]) == 1


def test_dry_run_changes_nothing(admin_client, data_dir, upload_dir):
    importer = Importer("", "", data_dir, dry_run=True, client=admin_client)
    importer.run([1], None)
    assert len(importer.report["создано"]) == 1
    assert admin_client.get("/tasks/by-external/kompege:101").status_code == 404


def test_update_rewrites_existing(admin_client, data_dir, upload_dir):
    run(admin_client, data_dir)
    task = admin_client.get("/tasks/by-external/kompege:101").json()
    admin_client.patch(f"/tasks/{task['id']}", json={"statement": "Испорчено вручную", "difficulty": 5})

    importer = Importer("", "", data_dir, dry_run=False, client=admin_client, update=True)
    importer.run([1], None)
    assert importer.report["обновлено"] == [f"kompege:101: задача {task['id']}"]
    fresh = admin_client.get(f"/tasks/{task['id']}/admin").json()
    assert fresh["statement"].startswith("Граф:") and fresh["difficulty"] == 60
    assert len(fresh["images"]) == 1  # старая картинка удалена, новая загружена
