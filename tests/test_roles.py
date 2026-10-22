"""Роли: управлять банком задач может только администратор."""

import pytest

from app.core.config import settings
from app.make_admin import set_role

NEW_TASK = {"exam_number": "1", "statement": "X", "correct_answer": "1"}


def test_guest_cannot_manage_bank(client):
    assert client.post("/tasks", json=NEW_TASK).status_code == 401
    assert client.patch("/tasks/1", json={"difficulty": 1}).status_code == 401
    assert client.delete("/tasks/1").status_code == 401
    assert client.get("/tasks/1/admin").status_code == 401


def test_student_cannot_manage_bank(client, login):
    me = login()
    for response in (
        client.post("/tasks", json=NEW_TASK, headers=me),
        client.patch("/tasks/1", json={"difficulty": 1}, headers=me),
        client.delete("/tasks/1", headers=me),
        client.get("/tasks/1/admin", headers=me),
    ):
        assert response.status_code == 403
        assert response.json()["detail"] == "Только для администратора"
    assert client.get("/tasks/1").status_code == 200  # задача на месте


def test_admin_can_manage_bank(admin_client):
    created = admin_client.post("/tasks", json=NEW_TASK)
    assert created.status_code == 201
    full = admin_client.get(f"/tasks/{created.json()['id']}/admin").json()
    assert full["correct_answer"] == "1"  # админу видны ответ и разбор
    assert admin_client.delete(f"/tasks/{full['id']}").status_code == 204


def test_role_in_profile(client, login, admin_client):
    assert client.get("/auth/me", headers=login()).json()["role"] == "student"
    assert admin_client.get("/auth/me").json()["role"] == "admin"


def test_admin_from_env(client, login, monkeypatch):
    monkeypatch.setattr(settings, "admin_telegram_ids", "555, 777")
    assert settings.admin_ids == {555, 777}
    me = login(777, "Хозяин")
    assert client.get("/auth/me", headers=me).json()["role"] == "admin"
    assert client.post("/tasks", json=NEW_TASK, headers=me).status_code == 201


@pytest.mark.parametrize("who", ["111", "@vasya"])
def test_make_admin_command(client, login, db, who):
    from app.modules.auth.service import TelegramProfile, upsert_user

    me = login(111, "Вася")
    upsert_user(db, TelegramProfile(telegram_id=111, first_name="Вася", username="Vasya"))  # у Васи есть @username
    assert "теперь admin" in set_role(who, "admin")
    assert client.get("/auth/me", headers=me).json()["role"] == "admin"
    assert "теперь student" in set_role(who, "student")
    assert client.post("/tasks", json=NEW_TASK, headers=me).status_code == 403


def test_make_admin_unknown_user():
    assert "не найден" in set_role("@nobody", "admin")
    assert "Не понимаю" in set_role("vasya", "admin")
