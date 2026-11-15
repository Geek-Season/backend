"""Архивная тема не попадает в ЕГЭ, но сохраняет историю и начисления."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.progress.models import Attempt
from app.modules.pts import service as pts
from app.modules.pts.models import PtsChange, UserNumberRating
from app.modules.tasks import service as tasks
from app.modules.tasks.models import ExamNumber, Task


def legacy(db):
    number = ExamNumber(code="legacy-23", title="Графы — архив", position=10023, is_active=False)
    task = Task(exam_number=number, statement="Граф", correct_answer="7", is_archived=True)
    user = User(telegram_id=2027001, first_name="Ученик")
    db.add_all([task, user])
    db.commit()
    return number, task, user


def test_hidden_topic_is_not_a_number(client, db):
    number, _, _ = legacy(db)
    assert number.code not in [n["code"] for n in client.get("/numbers").json()]
    assert client.get("/numbers/legacy-23/tasks").status_code == 404
    assert client.get("/rating?number=legacy-23").status_code == 404
    assert tasks.get_number(db, number.code) is None


def test_archived_topic_keeps_pts_without_forecast_slot(db):
    number, task, user = legacy(db)
    engine = pts._engine_readonly(db, user)
    assert number.code not in engine.typical()
    assert len(engine.typical()) == 25
    attempt = Attempt(user_id=user.id, task_id=task.id, answer="7", is_correct=True,
                      created_at=datetime.now(UTC))
    db.add(attempt)
    db.commit()
    outcome = pts.record_answer(db, user, task, attempt)
    assert outcome.counted == 1
    live = user.pts
    assert db.scalar(select(UserNumberRating).where(UserNumberRating.exam_number_id == number.id))
    pts.recompute_all(db)
    db.refresh(user)
    assert user.pts == pytest.approx(live)
    assert db.scalar(select(PtsChange).where(PtsChange.task_id == task.id)).exam_number_id == number.id
    first = user.pts
    pts.recompute_all(db)
    db.refresh(user)
    assert user.pts == pytest.approx(first)
    assert pts.record_answer(db, user, task, attempt).counted == 0


def test_current_titles_and_topics(client, db):
    titles = {n["code"]: n["title"] for n in client.get("/numbers").json()}
    assert titles["10"] == "Поиск в текстовом редакторе"
    assert titles["13"] == "IP-адресация и маски подсети"
    assert titles["23"] == "Исполнитель"
    assert db.scalar(select(Task).join(ExamNumber).where(ExamNumber.code == "13"))
    assert db.scalar(select(Task).join(ExamNumber).where(ExamNumber.code == "23"))
