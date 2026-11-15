"""Исторические номера сохраняются после переноса и архивации задач."""

import pytest
from sqlalchemy import select, update

from app.modules.auth.models import User
from app.modules.exam import service as exam
from app.modules.homework import service as homework
from app.modules.homework.models import HomeworkTask
from app.modules.tasks.models import ExamNumber, Task


def test_homework_history(db, admin_client, new_task):
    user = db.get(User, admin_client.get("/auth/me").json()["id"])
    task = db.get(Task, new_task["id"])
    old_code, old_title = task.exam_code, task.exam_number.title
    detail = homework.create(db, user, "История", None, [task.id], [user.id])
    homework.send(db, detail["id"])
    item = db.scalar(select(HomeworkTask))
    assert (item.number_code, item.number_title) == (old_code, old_title)
    task.exam_number = db.scalar(select(ExamNumber).where(ExamNumber.code == "2"))
    task.is_archived = True
    db.commit()
    assert homework.admin_list(db)[0]["numbers"] == [old_code]
    assert homework.my_list(db, user)[0]["numbers"] == [old_code]
    assert homework.admin_detail(db, detail["id"])["tasks"][0]["exam_number"] == old_code
    assert homework.results(db, detail["id"])["tasks"][0]["exam_number"] == old_code
    assert homework.my_detail(db, user, detail["id"])["items"][0]["task"].exam_number == old_code
    assert item.exam_title == old_title
    assert homework.answer(db, user, detail["id"], task.id, "42", None)["is_correct"]
    item.number_code = item.number_title = None
    assert item.exam_code == "2"
    assert item.exam_title == task.exam_number.title


def test_exam_history_and_archived_answer(db, admin_client, new_task, fake_redis):
    user_id = admin_client.get("/auth/me").json()["id"]
    db.execute(update(Task).where(Task.id != new_task["id"]).values(is_archived=True))
    db.commit()
    session, created = exam.start(db, fake_redis, user_id)
    assert created
    item = session.answers[0]
    old_code, old_title = item.task.exam_code, item.task.exam_number.title
    assert (item.number_code, item.number_title) == (old_code, old_title)
    item.task.exam_number = db.scalar(select(ExamNumber).where(ExamNumber.code == "2"))
    item.task.is_archived = True
    db.commit()
    view = exam.to_view(session)["items"][0]
    assert (view["exam_number"], view["title"]) == (old_code, old_title)
    assert view["task_number"] == "2"
    item.task.exam_number.is_active = False
    db.commit()
    assert exam.to_view(session)["items"][0]["task_number"] is None
    assert exam.save_answer(db, fake_redis, session, item.position, "42").answer == "42"
    exam.finish(db, fake_redis, session)
    assert exam.to_view(session)["items"][0]["exam_number"] == old_code
    item.number_code = item.number_title = None
    assert item.exam_code == "2"
    assert item.exam_title == item.task.exam_number.title


def test_inactive_numbers_excluded(db, admin_client, new_task, fake_redis):
    task = db.get(Task, new_task["id"])
    db.execute(update(Task).where(Task.id != task.id).values(is_archived=True))
    task.exam_number.is_active = False
    db.commit()
    user = db.get(User, admin_client.get("/auth/me").json()["id"])
    assert homework.picker(db, None, "", [], 20, 0)["total"] == 0
    with pytest.raises(homework.Invalid):
        homework.random_tasks(db, "1", 1, [], [])
    with pytest.raises(homework.Invalid):
        homework.create(db, user, "Новая", None, [task.id], [])
    db.rollback()
    with pytest.raises(exam.NoTasks):
        exam.start(db, fake_redis, user.id)


def test_exam_parts_snapshot(db, admin_client, fake_redis):
    from tests.test_parts import game_task

    task_id = game_task(admin_client)["id"]
    db.execute(update(Task).where(Task.id != task_id).values(is_archived=True))
    db.commit()
    user_id = admin_client.get("/auth/me").json()["id"]
    session, _ = exam.start(db, fake_redis, user_id)
    assert [a.number_code for a in session.answers] == ["19", "20", "21"]
    for item in session.answers:
        assert item.number_title == item.task.exam_number.title
        item.number_code = None
        assert item.exam_code == item.part.number
