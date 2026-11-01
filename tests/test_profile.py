"""Настройки профиля: имя с пробелами, пол, аватарка (своя и из Telegram)."""

import pytest

from app.core import telegram
from app.core.config import settings
from app.modules.auth import avatars

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def me(client, headers):
    return client.get("/auth/me", headers=headers).json()


def upload(client, headers, content=PNG, name="a.png"):
    return client.put("/auth/me/avatar", files={"file": (name, content, "image/png")}, headers=headers)


@pytest.fixture
def no_telegram(monkeypatch):
    """Фото из Telegram в фоне не запрашивается; список вызовов — для проверки."""
    calls = []
    monkeypatch.setattr(avatars, "refresh_from_telegram", lambda user_id: calls.append(user_id))
    return calls


def test_name_with_spaces(client, login):
    h = login()
    body = client.patch("/auth/me", json={"nickname": "  Иван   Петров "}, headers=h).json()
    assert body["display_name"] == "Иван Петров"


def test_gender_and_partial_update(client, login):
    h = login()
    client.patch("/auth/me", json={"nickname": "Котик"}, headers=h)
    assert me(client, h)["gender"] is None
    assert client.patch("/auth/me", json={"gender": "f"}, headers=h).json()["gender"] == "f"
    assert me(client, h)["nickname"] == "Котик"  # пол сменили — имя не тронуто
    assert client.patch("/auth/me", json={"gender": None}, headers=h).json()["gender"] is None
    assert client.patch("/auth/me", json={"gender": "x"}, headers=h).status_code == 422
    assert "gender" not in client.get("/rating?period=all").text  # пол нигде не публикуется


def test_custom_avatar(client, login, no_telegram):
    h = login()
    assert me(client, h)["avatar_url"] is None
    body = upload(client, h).json()
    assert body["has_custom_avatar"] is True and body["avatar_url"].startswith("/avatars/")
    picture = client.get(body["avatar_url"])
    assert picture.status_code == 200 and picture.content == PNG
    assert picture.headers["content-type"] == "image/png"
    assert "immutable" in picture.headers["cache-control"]

    second = upload(client, h, JPG, "b.jpg").json()["avatar_url"]
    assert second != body["avatar_url"] and second.endswith(".jpg")
    assert client.get(body["avatar_url"]).status_code == 404  # старый файл удалён


@pytest.mark.parametrize("content, message", [
    (b"GIF89a" + b"\x00" * 20, "PNG, JPEG или WebP"),
    (b"<svg onload=alert(1)>", "PNG, JPEG или WebP"),
    (b"", "Пустой"),
    (PNG + b"\x00" * (avatars.MAX_BYTES + 1), "2 МБ"),
])
def test_bad_avatar(client, login, content, message):
    response = upload(client, login(), content)
    assert response.status_code == 422 and message in response.json()["detail"]


def test_return_telegram_photo(client, login, no_telegram):
    h = login()
    url = upload(client, h).json()["avatar_url"]
    body = client.delete("/auth/me/avatar", headers=h).json()
    assert body["has_custom_avatar"] is False and body["avatar_url"] is None
    assert client.get(url).status_code == 404
    assert no_telegram == [body["id"], body["id"]]  # при входе и после «вернуть» — фото из Telegram в фоне


def test_admin_resets_avatar(client, admin_client, login, no_telegram, ranked):
    h = login()
    upload(client, h)
    user_id = me(client, h)["id"]
    ranked(user_id)
    row = client.get("/rating?period=all").json()["top"][0]
    assert row["avatar_url"].startswith("/avatars/") and row["has_custom_avatar"] is True
    assert client.delete(f"/auth/users/{user_id}/avatar", headers=h).status_code == 403
    assert admin_client.delete(f"/auth/users/{user_id}/avatar").status_code == 204
    assert me(client, h)["has_custom_avatar"] is False
    assert admin_client.delete("/auth/users/999999/avatar").status_code == 404


def test_avatar_names_checked(client):
    for name in ("../../etc/passwd", "x.svg", "0" * 32 + ".gif"):
        assert client.get(f"/avatars/{name}").status_code == 404


def test_login_asks_telegram_photo(client, login, no_telegram):
    h = login()
    assert no_telegram == [me(client, h)["id"]]


# ---------- Фото из Telegram ----------

@pytest.fixture
def fake_telegram(monkeypatch):
    state = {"photos": [[{"file_id": "s", "file_unique_id": "u1", "width": 160},
                         {"file_id": "m", "file_unique_id": "u2", "width": 320},
                         {"file_id": "l", "file_unique_id": "u3", "width": 640}]], "downloads": 0}

    def call(method, payload=None, timeout=10):
        if method == "getUserProfilePhotos":
            return {"total_count": len(state["photos"]), "photos": state["photos"]}
        assert method == "getFile" and payload["file_id"] == "m"  # размер ближе к 320
        return {"file_path": "photos/file_1.jpg"}

    def download(path, limit, timeout=15):
        state["downloads"] += 1
        return JPG

    monkeypatch.setattr(settings, "bot_token", "test")
    monkeypatch.setattr(telegram, "call", call)
    monkeypatch.setattr(telegram, "download", download)
    return state


def test_telegram_photo(client, login, fake_telegram):
    h = login()  # вход → фон: фото скачано
    body = me(client, h)
    assert body["avatar_url"].endswith(".jpg") and body["has_custom_avatar"] is False
    assert fake_telegram["downloads"] == 1

    avatars.refresh_from_telegram(body["id"])  # фото то же — заново не качаем
    assert fake_telegram["downloads"] == 1

    fake_telegram["photos"] = []  # убрал фото в Telegram
    avatars.refresh_from_telegram(body["id"])
    assert me(client, h)["avatar_url"] is None


def test_telegram_photo_does_not_replace_custom(client, login, fake_telegram):
    h = login()
    url = upload(client, h).json()["avatar_url"]
    avatars.refresh_from_telegram(me(client, h)["id"])
    assert me(client, h)["avatar_url"] == url


def test_telegram_errors_are_quiet(client, login, monkeypatch):
    monkeypatch.setattr(settings, "bot_token", "test")

    def broken(*args, **kwargs):
        raise telegram.TelegramTemporaryError("нет сети")

    monkeypatch.setattr(telegram, "call", broken)
    h = login()  # вход работает, хоть Telegram и недоступен
    assert me(client, h)["avatar_url"] is None
