"""Выгрузка kompege.ru: недоступная картинка не роняет выгрузку, а задача с ней не попадает в импорт."""

import json

import httpx2 as httpx

from scripts.kompege import export
from scripts.kompege.import_ import Importer

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
VK = "https://sun9-79.userapi.com/impg/broken.png"

ITEMS = [
    {"taskId": 601, "number": 6, "comment": "", "key": "15", "video": "",
     "text": f'<p>Черепаха.</p><p><img src="{VK}" /></p>', "files": []},
    {"taskId": 602, "number": 6, "comment": "", "key": "16", "video": "",
     "text": '<p>Черепаха.</p><p><img src="/files/ok.png" /></p>', "files": []},
    {"taskId": 603, "number": 6, "comment": "", "key": "17", "video": "",
     "text": "<p>Черепаха.</p>", "files": [{"url": "/files/six.txt", "name": "6.txt"}]},
]


def fake_site(calls: list):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if request.url.host == "sun9-79.userapi.com":
            raise httpx.ConnectError("EOF occurred in violation of protocol (_ssl.c:1032)", request=request)
        if url.endswith("/api/v1/task/number/6"):
            return httpx.Response(200, json=ITEMS)
        if url.endswith("/files/ok.png"):
            return httpx.Response(200, content=PNG)
        if url.endswith("/files/six.txt"):
            return httpx.Response(200, content=b"1 2 3\n")
        return httpx.Response(404)

    http = export.Polite(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    return http


def test_broken_image_does_not_stop_export(tmp_path):
    calls = []
    stats = export.export_number(fake_site(calls), 6, tmp_path, None)

    # Выгрузка дошла до конца: остальные задачи скачаны.
    assert stats["выгружено"] == 3 and stats["скачано файлов"] == 2
    assert (tmp_path / "files" / "603" / "6.txt").read_bytes() == b"1 2 3\n"
    # Картинку ВК пытались скачать трижды и сдались.
    assert sum(VK in url for url in calls) == 3
    assert len(stats["failed"]) == 1
    assert stats["failed"][0].startswith(f"kompege:601: {VK} — ConnectError: EOF occurred")

    rows = {row["external_id"]: row for row in json.loads((tmp_path / "preview" / "6.json").read_text("utf-8"))}
    assert rows["kompege:601"]["skip"] == f"не скачались: картинка {VK}"
    assert rows["kompege:602"]["skip"] is None and rows["kompege:603"]["skip"] is None


def test_missing_attachment_404_is_reported(tmp_path):
    items = [{**ITEMS[2], "files": [{"url": "/files/gone.txt", "name": "6.txt"}]}]

    def handler(request):
        if str(request.url).endswith("/api/v1/task/number/6"):
            return httpx.Response(200, json=items)
        return httpx.Response(404)

    http = export.Polite(delay=0)
    http.client = httpx.Client(transport=httpx.MockTransport(handler))
    stats = export.export_number(http, 6, tmp_path, None)
    assert stats["failed"] == ["kompege:603: https://kompege.ru/files/gone.txt — ответ 404"]
    assert stats["не пойдут в импорт"] == 1


def test_import_skips_task_with_missing_image(admin_client, tmp_path, upload_dir):
    export.export_number(fake_site([]), 6, tmp_path, None)

    importer = Importer("", "", tmp_path, dry_run=False, client=admin_client)
    importer.run([6], None)
    report = importer.report

    assert not report["ошибки"]
    assert len(report["создано"]) == 2
    assert report["проверить вручную"] == [
        f"kompege:601: не импортирована, не скачались: картинка {VK} (повторите export или добавьте вручную)"
    ]
    # Задачу без картинки на сайт не создали — ни целиком, ни наполовину.
    assert admin_client.get("/tasks/by-external/kompege:601").status_code == 404
    task = admin_client.get("/tasks/by-external/kompege:602").json()
    assert len(task["images"]) == 1 and "pending:" not in task["statement"]
