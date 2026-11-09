"""Порядок уже импортированных задач kompege: перестановка created_at внутри номера.

Эндпоинт POST /tasks/reorder-kompege (для сервера без консоли) и скрипт scripts.kompege.reorder.
"""

import pytest
from sqlalchemy import select

from app.modules.tasks.models import Task
from scripts.kompege.reorder import reorder

URL = "/tasks/reorder-kompege"
BACKWARDS = ["К1000", "К2000", "Ручная", "К3000"]  # как после старого импорта, сверху — старая
FIXED = ["К3000", "К2000", "Ручная", "К1000"]


def create(admin_client, statement: str, external_id: str | None = None) -> int:
    payload = {"exam_number": "2", "statement": statement, "correct_answer": "1"}
    if external_id:
        payload["external_id"] = external_id
    return admin_client.post("/tasks", json=payload).json()["id"]


def listed(client) -> list[str]:
    page = client.get("/numbers/2/tasks", params={"sort": "new", "limit": 100}).json()
    return [t["statement"] for t in page["items"] if t["statement"].startswith(("К", "Ручная"))]


@pytest.fixture
def imported_backwards(admin_client):
    # Как старый импорт: kompege отдал от новых к старым; между ними — задача, добавленная вручную.
    create(admin_client, "К3000", "kompege:3000")
    create(admin_client, "Ручная", None)
    create(admin_client, "К2000", "kompege:2000")
    create(admin_client, "К1000", "kompege:1000")
    create(admin_client, "Чужая", "other:1")  # другой банк — не трогаем


def journal(admin_client) -> list[dict]:
    return [a for a in admin_client.get("/admin/actions").json()["items"] if a["action"] == "tasks.reorder"]


def test_dry_run_by_default_changes_nothing(admin_client, client, imported_backwards):
    body = admin_client.post(URL).json()
    assert body == {"dry_run": True, "moved": 2, "numbers": [{"number": "2", "total": 3, "moved": 2}]}
    assert listed(client) == BACKWARDS
    assert journal(admin_client) == []


def test_reorder_fixes_order_keeps_manual_task_and_logs(admin_client, client, imported_backwards):
    body = admin_client.post(URL, params={"dry_run": False}).json()
    assert body["dry_run"] is False and body["moved"] == 2  # К2000 осталась на своём времени
    assert listed(client) == FIXED

    entries = journal(admin_client)
    assert len(entries) == 1 and entries[0]["user"] is None
    assert entries[0]["details"] == {"source": "kompege", "moved": 2, "numbers": {"2": 2}}
    assert entries[0]["text"] == "переставил задачи kompege по порядку банка: сменили место 2 (№ 2: 2)"

    # Повторный вызов ничего не меняет.
    assert admin_client.post(URL, params={"dry_run": False}).json()["moved"] == 0
    assert listed(client) == FIXED


def test_only_admin(client, login, imported_backwards):
    assert client.post(URL).status_code == 401
    assert client.post(URL, headers=login()).status_code == 403


def test_same_time_is_conflict_and_changes_nothing(db, admin_client, client, imported_backwards):
    tasks = db.scalars(select(Task).where(Task.external_id.in_(["kompege:1000", "kompege:2000"]))).all()
    tasks[0].created_at = tasks[1].created_at
    db.commit()
    before = listed(client)

    response = admin_client.post(URL, params={"dry_run": False})
    assert response.status_code == 409 and "номер 2" in response.json()["detail"]
    assert listed(client) == before and journal(admin_client) == []


def test_script_does_the_same(db, client, imported_backwards):
    assert [n["moved"] for n in reorder(db, apply=False)] == [2]
    db.expire_all()
    assert listed(client) == BACKWARDS
    assert [n["moved"] for n in reorder(db, apply=True)] == [2]
    assert listed(client) == FIXED
