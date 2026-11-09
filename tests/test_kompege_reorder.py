"""Порядок уже импортированных задач kompege: перестановка created_at внутри номера."""

import pytest
from sqlalchemy import select

from app.modules.tasks.models import Task
from scripts.kompege.reorder import SameTimeError, reorder


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


def test_newest_kompege_task_on_top_manual_keeps_place(db, client, imported_backwards):
    assert listed(client) == ["К1000", "К2000", "Ручная", "К3000"]

    reports = reorder(db, apply=True)
    assert [(r.code, r.total, r.moved) for r in reports] == [("2", 3, 2)]  # К2000 осталась на своём времени
    assert listed(client) == ["К3000", "К2000", "Ручная", "К1000"]

    # Повторный запуск ничего не меняет.
    assert [r.moved for r in reorder(db, apply=True)] == [0]


def test_dry_run_changes_nothing(db, client, imported_backwards):
    assert [r.moved for r in reorder(db, apply=False)] == [2]
    db.expire_all()
    assert listed(client) == ["К1000", "К2000", "Ручная", "К3000"]


def test_same_time_stops_without_changes(db, client, imported_backwards):
    tasks = db.scalars(select(Task).where(Task.external_id.in_(["kompege:1000", "kompege:2000"]))).all()
    tasks[0].created_at = tasks[1].created_at
    db.commit()
    before = listed(client)

    with pytest.raises(SameTimeError, match="номер 2"):
        reorder(db, apply=True)
    db.rollback()
    assert listed(client) == before
