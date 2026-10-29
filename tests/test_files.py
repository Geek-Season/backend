"""Картинки и файлы к задачам: загрузка, проверки, права, удаление, скачивание."""

import pytest

from app.core.config import settings

# Минимальные «настоящие» картинки: по первым байтам сервер узнаёт тип.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
GIF = b"GIF89a" + b"\x00" * 64
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 64
XLSX = b"PK\x03\x04" + b"\x00" * 64  # содержимое файлов-заданий сервер не разбирает


def upload(client, task_id, name, data, kind):
    return client.post(f"/tasks/{task_id}/files", files={"file": (name, data)}, data={"kind": kind})


def stored_files(upload_dir):
    return sorted(p.name for p in upload_dir.iterdir()) if upload_dir.exists() else []


# ---------- Загрузка ----------

def test_upload_image_shows_in_task(admin_client, client, new_task, upload_dir):
    response = upload(admin_client, new_task["id"], "граф.png", PNG, "image")
    assert response.status_code == 201, response.text
    image = response.json()
    assert image["kind"] == "image"
    assert image["name"] == "граф.png"
    assert image["size"] == len(PNG)
    assert image["content_type"] == "image/png"
    assert image["url"].startswith("/files/") and image["url"].endswith(".png")

    task = client.get(f"/tasks/{new_task['id']}").json()  # ученик видит картинку
    assert [i["id"] for i in task["images"]] == [image["id"]]
    assert task["attachments"] == []
    assert stored_files(upload_dir) == [image["url"].removeprefix("/files/")]


def test_upload_attachment(admin_client, client, new_task):
    response = upload(admin_client, new_task["id"], "9.xlsx", XLSX, "attachment")
    assert response.status_code == 201, response.text
    task = client.get(f"/tasks/{new_task['id']}").json()
    assert [a["name"] for a in task["attachments"]] == ["9.xlsx"]
    assert task["attachments"][0]["content_type"].startswith("application/vnd.openxmlformats")
    assert task["images"] == []


@pytest.mark.parametrize("data, ext", [(PNG, ".png"), (JPEG, ".jpg"), (GIF, ".gif"), (WEBP, ".webp")])
def test_image_type_detected_by_content(admin_client, new_task, data, ext):
    # Имя файла может врать — тип и расширение берутся из содержимого.
    response = upload(admin_client, new_task["id"], "picture.bin", data, "image")
    assert response.status_code == 201, response.text
    assert response.json()["url"].endswith(ext)


def test_files_keep_upload_order(admin_client, client, new_task):
    for name in ("a.txt", "b.txt", "c.txt"):
        assert upload(admin_client, new_task["id"], name, b"1 2 3", "attachment").status_code == 201
    task = client.get(f"/tasks/{new_task['id']}").json()
    assert [a["name"] for a in task["attachments"]] == ["a.txt", "b.txt", "c.txt"]


def test_files_in_number_page_and_admin_view(admin_client, client, new_task):
    upload(admin_client, new_task["id"], "1.png", PNG, "image")
    upload(admin_client, new_task["id"], "1.txt", b"data", "attachment")
    items = client.get("/numbers/1/tasks").json()["items"]
    task = next(t for t in items if t["id"] == new_task["id"])
    assert len(task["images"]) == 1 and len(task["attachments"]) == 1
    full = admin_client.get(f"/tasks/{new_task['id']}/admin").json()
    assert len(full["images"]) == 1 and len(full["attachments"]) == 1


def test_parts_task_can_have_files(admin_client, client):
    task = client.get("/numbers/19-21/tasks").json()["items"][0]
    assert upload(admin_client, task["id"], "игра.png", PNG, "image").status_code == 201
    assert len(client.get(f"/tasks/{task['id']}").json()["images"]) == 1


# ---------- Что не принимается ----------

def test_fake_image_rejected(admin_client, new_task, upload_dir):
    response = upload(admin_client, new_task["id"], "virus.png", b"MZ\x90\x00 not an image", "image")
    assert response.status_code == 422
    assert "не картинка" in response.json()["detail"]
    assert stored_files(upload_dir) == []


@pytest.mark.parametrize("name", ["page.html", "logo.svg", "run.exe", "script.js", "noext"])
def test_dangerous_attachment_rejected(admin_client, new_task, upload_dir, name):
    response = upload(admin_client, new_task["id"], name, b"<script>alert(1)</script>", "attachment")
    assert response.status_code == 422
    assert "тип файла" in response.json()["detail"]
    assert stored_files(upload_dir) == []


def test_svg_not_accepted_as_image(admin_client, new_task):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert upload(admin_client, new_task["id"], "x.svg", svg, "image").status_code == 422


def test_too_big_rejected(admin_client, new_task, upload_dir, monkeypatch):
    monkeypatch.setattr(settings, "max_attachment_mb", 1)
    big = b"1" * (1024 * 1024 + 1)
    response = upload(admin_client, new_task["id"], "27_B.txt", big, "attachment")
    assert response.status_code == 422
    assert "больше 1 МБ" in response.json()["detail"]
    assert stored_files(upload_dir) == []
    exact = b"1" * (1024 * 1024)  # ровно лимит — можно
    assert upload(admin_client, new_task["id"], "27_A.txt", exact, "attachment").status_code == 201


def test_empty_file_rejected(admin_client, new_task):
    response = upload(admin_client, new_task["id"], "empty.txt", b"", "attachment")
    assert response.status_code == 422
    assert response.json()["detail"] == "Файл пустой"


def test_files_per_task_limited(admin_client, new_task, monkeypatch):
    monkeypatch.setattr(settings, "max_files_per_kind", 2)
    for i in range(2):
        assert upload(admin_client, new_task["id"], f"{i}.png", PNG, "image").status_code == 201
    response = upload(admin_client, new_task["id"], "3.png", PNG, "image")
    assert response.status_code == 422
    assert "не больше 2 картинок" in response.json()["detail"]
    # лимит считается отдельно для картинок и для файлов
    assert upload(admin_client, new_task["id"], "1.txt", b"1", "attachment").status_code == 201


def test_wrong_kind_or_missing_file(admin_client, new_task):
    assert upload(admin_client, new_task["id"], "1.png", PNG, "video").status_code == 422
    assert admin_client.post(f"/tasks/{new_task['id']}/files", data={"kind": "image"}).status_code == 422


def test_upload_to_missing_or_archived_task(admin_client, new_task):
    assert upload(admin_client, 999999, "1.png", PNG, "image").status_code == 404
    admin_client.delete(f"/tasks/{new_task['id']}")
    assert upload(admin_client, new_task["id"], "1.png", PNG, "image").status_code == 404


def test_original_name_cleaned(admin_client, new_task, upload_dir):
    response = upload(admin_client, new_task["id"], "../../etc/passwd.txt", b"x", "attachment")
    assert response.status_code == 201
    assert response.json()["name"] == "passwd.txt"
    stored = stored_files(upload_dir)
    assert len(stored) == 1 and stored[0] != "passwd.txt"  # на диске — случайное имя


# ---------- Права ----------

def test_guest_and_student_cannot_upload_or_delete(admin_client, client, login, new_task):
    file_id = upload(admin_client, new_task["id"], "1.png", PNG, "image").json()["id"]
    assert upload(client, new_task["id"], "1.png", PNG, "image").status_code == 401
    assert client.delete(f"/tasks/{new_task['id']}/files/{file_id}").status_code == 401
    me = login()
    response = client.post(
        f"/tasks/{new_task['id']}/files", files={"file": ("1.png", PNG)}, data={"kind": "image"}, headers=me
    )
    assert response.status_code == 403
    assert client.delete(f"/tasks/{new_task['id']}/files/{file_id}", headers=me).status_code == 403


# ---------- Удаление ----------

def test_delete_file(admin_client, client, new_task, upload_dir):
    image = upload(admin_client, new_task["id"], "1.png", PNG, "image").json()
    assert admin_client.delete(f"/tasks/{new_task['id']}/files/{image['id']}").status_code == 204
    assert client.get(f"/tasks/{new_task['id']}").json()["images"] == []
    assert stored_files(upload_dir) == []  # и с диска тоже
    assert client.get(image["url"]).status_code == 404
    assert admin_client.delete(f"/tasks/{new_task['id']}/files/{image['id']}").status_code == 404


def test_delete_file_of_other_task(admin_client, new_task):
    other = admin_client.post("/tasks", json={"exam_number": "2", "statement": "Y", "correct_answer": "1"}).json()
    file_id = upload(admin_client, other["id"], "1.png", PNG, "image").json()["id"]
    # id файла верный, но он принадлежит другой задаче
    assert admin_client.delete(f"/tasks/{new_task['id']}/files/{file_id}").status_code == 404


def test_archived_task_keeps_files(admin_client, client, new_task):
    image = upload(admin_client, new_task["id"], "1.png", PNG, "image").json()
    admin_client.delete(f"/tasks/{new_task['id']}")
    # Архивная задача может быть в истории пробников — картинка должна открываться.
    assert client.get(image["url"]).status_code == 200


# ---------- Скачивание ----------

def test_download_image_inline(admin_client, client, new_task):
    image = upload(admin_client, new_task["id"], "граф.png", PNG, "image").json()
    response = client.get(image["url"])  # картинки доступны и гостю
    assert response.status_code == 200
    assert response.content == PNG
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_download_attachment_with_original_name(admin_client, client, new_task):
    attachment = upload(admin_client, new_task["id"], "задание 9.xlsx", XLSX, "attachment").json()
    response = client.get(attachment["url"])
    assert response.status_code == 200
    assert response.content == XLSX
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment")
    assert "%D0%B7%D0%B0%D0%B4%D0%B0%D0%BD%D0%B8%D0%B5%209.xlsx" in disposition  # «задание 9.xlsx»


@pytest.mark.parametrize("path", [
    "/files/nope.png",
    "/files/..%2F..%2Fapp%2Fmain.py",
    "/files/" + "a" * 32 + ".png",
])
def test_download_unknown_file(client, path):
    assert client.get(path).status_code == 404


def test_download_missing_on_disk(admin_client, client, new_task, upload_dir):
    image = upload(admin_client, new_task["id"], "1.png", PNG, "image").json()
    (upload_dir / image["url"].removeprefix("/files/")).unlink()
    assert client.get(image["url"]).status_code == 404


# ---------- Пробник ----------

def test_exam_items_have_files(admin_client, client, login):
    task_id = client.get("/numbers/5/tasks").json()["items"][0]["id"]
    upload(admin_client, task_id, "5.png", PNG, "image")
    upload(admin_client, task_id, "5.txt", b"data", "attachment")
    me = login()
    exam = client.post("/exam/start", headers=me).json()
    item = next(i for i in exam["items"] if i["task_id"] == task_id)
    assert len(item["images"]) == 1
    assert [a["name"] for a in item["attachments"]] == ["5.txt"]
