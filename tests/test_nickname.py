"""Ник в профиле: показывается вместо имени из Telegram везде на сайте."""

import pytest

from app.modules.auth import nickname

TASK_17 = 8


def set_nick(client, headers, value):
    return client.patch("/auth/me", json={"nickname": value}, headers=headers)


def test_nickname_replaces_name_everywhere(client, login):
    me = login(111, "Вася")
    assert client.get("/auth/me", headers=me).json()["display_name"] == "Вася"

    response = set_nick(client, me, "  Котик_42  ")
    assert response.status_code == 200
    assert response.json()["nickname"] == "Котик_42" and response.json()["display_name"] == "Котик_42"
    assert client.get("/auth/me", headers=me).json()["first_name"] == "Вася"  # имя из Telegram себе видно

    client.post(f"/tasks/{TASK_17}/check", json={"answer": "2 20"}, headers=me)
    row = client.get("/rating?period=all").json()["top"][0]
    assert row["name"] == "Котик_42" and row["has_nickname"] is True
    assert "Вася" not in client.get("/rating?period=all").text  # имя из Telegram в рейтинге не светится


def test_remove_nickname(client, login):
    me = login()
    set_nick(client, me, "kotik")
    for empty in (None, "", "   "):
        assert set_nick(client, me, empty).json()["display_name"] == "Вася"
        set_nick(client, me, "kotik")


def test_same_nickname_allowed(client, login):
    assert set_nick(client, login(111, "Вася"), "Саша").status_code == 200
    assert set_nick(client, login(222, "Петя"), "Саша").status_code == 200


@pytest.mark.parametrize("value", ["ab", "a" * 21, "кот ик", "кот!", "😀😀😀", "___", "-_-"])
def test_bad_form(client, login, value):
    response = set_nick(client, login(), value)
    assert response.status_code == 422
    assert response.json()["detail"]  # понятное сообщение


@pytest.mark.parametrize("value", ["хуйло", "XyЙ_123", "п1зда", "блядина", "fuck_you", "SHITman", "ДолбоЁб", "ебанат", "kot_ebal", "pizdec", "huynya", "blyat"])
def test_profanity_rejected(value):
    with pytest.raises(nickname.NicknameError, match="нельзя"):
        nickname.clean(value)


@pytest.mark.parametrize("value", ["команда", "хлебать", "Саша", "kotik_42", "Ёжик-007", "Учебник", "Матвей", "Sukharev", "рубля", "колебать", "погребальный", "скребок", "Yuliya", "Shura", "Chubaka", "Nikita_2009", "Mikhail", "Sergey", "Xenia", "Скипидар", "Сучков"])
def test_normal_names_pass(value):
    assert nickname.clean(value) == value


def test_extra_fields_forbidden(client, login):
    me = login()
    assert client.patch("/auth/me", json={"nickname": "kotik", "role": "admin"}, headers=me).status_code == 422
    assert client.get("/auth/me", headers=me).json()["role"] == "student"
    assert client.patch("/auth/me", json={"nickname": "kotik"}).status_code == 401


def test_admin_resets_nickname(client, admin_client, login):
    me = login()
    set_nick(client, me, "kotik")
    user_id = client.get("/auth/me", headers=me).json()["id"]
    assert client.delete(f"/auth/users/{user_id}/nickname", headers=me).status_code == 403  # не админ
    assert admin_client.delete(f"/auth/users/{user_id}/nickname").status_code == 204
    assert client.get("/auth/me", headers=me).json()["nickname"] is None
    assert admin_client.delete("/auth/users/999999/nickname").status_code == 404


def test_login_keeps_nickname(client, login):
    me = login(111, "Вася")
    set_nick(client, me, "kotik")
    again = login(111, "Василий")  # вошёл снова, имя в Telegram сменилось
    body = client.get("/auth/me", headers=again).json()
    assert body["nickname"] == "kotik" and body["first_name"] == "Василий"
