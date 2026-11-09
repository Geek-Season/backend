"""Логика домашек. Про HTTP ничего не знает: бросает свои исключения, коды выбирает router.py.

Ответ на задачу домашки записывается ОДНОЙ транзакцией: строка homework_answers,
попытка в attempts и изменение PTS. Строка назначения ученика блокируется
(SELECT … FOR UPDATE), поэтому двойной клик или ответ одновременно со сдачей
не запишут две попытки и не начислят PTS дважды: второй запрос дождётся первого
и получит «уже отвечено».
"""

from datetime import UTC, datetime

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.modules.auth.models import User
from app.modules.homework.models import Homework, HomeworkAnswer, HomeworkAssignment, HomeworkTask
from app.modules.progress.models import Attempt
from app.modules.pts.schemas import rank_read
from app.modules.tasks import service as tasks
from app.modules.tasks.models import ExamNumber, Task
from app.modules.tasks.schemas import TaskRead


class NotFound(Exception):
    """Нет такой домашки (или она не этому ученику, или ещё черновик)."""


class Conflict(Exception):
    """Действие невозможно в текущем состоянии: уже отвечено, уже сдана, уже отправлена…"""


class Invalid(Exception):
    """Неверные данные: нет такой задачи или ученика, повторы, пустая домашка."""


def _now() -> datetime:
    return datetime.now(UTC)


def _numbers(homework: Homework) -> list[str]:
    """Коды номеров в порядке задач, без повторов."""
    seen: list[str] = []
    for item in homework.tasks:
        code = item.task.exam_code
        if code not in seen:
            seen.append(code)
    return seen


def _parts_of(result: dict) -> list[dict] | None:
    if not result["parts"]:
        return None
    return [{"number": p["number"], "answer": p["answer"], "is_correct": p["is_correct"]} for p in result["parts"]]


# ---------- Ученик ----------

def _my_assignments(user_id: int):
    return (
        select(HomeworkAssignment)
        .join(Homework, Homework.id == HomeworkAssignment.homework_id)
        .where(HomeworkAssignment.user_id == user_id, Homework.sent_at.is_not(None))
    )


def _assignment(db: Session, user: User, homework_id: int, lock: bool = False) -> HomeworkAssignment:
    query = _my_assignments(user.id).where(HomeworkAssignment.homework_id == homework_id)
    if lock:
        query = query.with_for_update(of=HomeworkAssignment)
    assignment = db.scalar(query)
    if assignment is None:
        raise NotFound
    return assignment


def _counts(assignment: HomeworkAssignment) -> tuple[int, int]:
    return len(assignment.answers), sum(1 for a in assignment.answers if a.is_correct)


def my_list(db: Session, user: User) -> list[dict]:
    """Домашки ученика: сначала несданные, внутри — новые сверху."""
    assignments = db.scalars(
        _my_assignments(user.id).options(
            selectinload(HomeworkAssignment.homework)
            .selectinload(Homework.tasks)
            .selectinload(HomeworkTask.task)
            .joinedload(Task.exam_number),
            selectinload(HomeworkAssignment.answers),
        )
    ).all()
    cards = []
    for a in assignments:
        hw = a.homework
        answered, correct = _counts(a)
        cards.append({
            "id": hw.id, "title": hw.title, "comment": hw.comment, "numbers": _numbers(hw),
            "total": len(hw.tasks), "answered": answered, "correct": correct,
            "sent_at": hw.sent_at, "submitted_at": a.submitted_at,
        })
    cards.sort(key=lambda c: (c["submitted_at"] is not None, -c["sent_at"].timestamp()))
    return cards


def my_detail(db: Session, user: User, homework_id: int) -> dict:
    assignment = _assignment(db, user, homework_id)
    hw = assignment.homework
    answers = {a.task_id: a for a in assignment.answers}
    submitted = assignment.submitted_at is not None
    items = []
    for item in hw.tasks:
        task = item.task  # архивная задача остаётся в домашке
        answer = answers.get(task.id)
        items.append({
            "position": item.position,
            "task": TaskRead.model_validate(task),
            "answer": answer.answer if answer else None,
            "is_correct": answer.is_correct if answer else None,
            "parts": answer.parts if answer else None,
            "solution": tasks.solution(task) if submitted else None,
        })
    answered, correct = _counts(assignment)
    return {
        "id": hw.id, "title": hw.title, "comment": hw.comment, "sent_at": hw.sent_at,
        "submitted_at": assignment.submitted_at, "total": len(hw.tasks),
        "answered": answered, "correct": correct, "items": items,
    }


def answer(
    db: Session, user: User, homework_id: int, task_id: int, given: str | None, given_parts: dict[str, str] | None
) -> dict:
    """Единственная попытка. Бросает NotFound, Conflict, tasks.WrongAnswerFormat."""
    try:
        assignment = _assignment(db, user, homework_id, lock=True)
        if assignment.submitted_at is not None:
            raise Conflict("Домашка уже сдана")
        in_homework = db.scalar(
            select(HomeworkTask).where(HomeworkTask.homework_id == homework_id, HomeworkTask.task_id == task_id)
        )
        if in_homework is None:
            raise NotFound
        already = db.scalar(
            select(HomeworkAnswer.task_id).where(
                HomeworkAnswer.assignment_id == assignment.id, HomeworkAnswer.task_id == task_id
            )
        )
        if already is not None:
            raise Conflict("На эту задачу уже есть ответ")

        task = in_homework.task
        result = tasks.check_answer(task, given, given_parts)
        db.add(HomeworkAnswer(
            assignment_id=assignment.id, task_id=task.id, answer=tasks.answer_text(result, given),
            is_correct=result["is_correct"], parts=_parts_of(result),
        ))
        db.flush()  # первичный ключ — последний рубеж против второй попытки
        first_solve, outcome = tasks.record_user_answer(db, user, task, result, given)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise Conflict("На эту задачу уже есть ответ") from None
    except BaseException:
        db.rollback()  # ничего из этого ответа не остаётся: ни попытки, ни PTS
        raise

    tasks.publish_answered(user.id, task.id, result["is_correct"], first_solve)
    return {
        "task_id": task.id,
        "is_correct": result["is_correct"],
        "parts": _parts_of(result) or [],
        "pts_delta": round(outcome.delta, 1) if outcome.counted else None,
        "pts_repeat": not outcome.counted,
        "rank_up": rank_read(outcome.rank_up),
    }


def submit(db: Session, user: User, homework_id: int) -> dict:
    """Сдать. Пропущенные задачи считаются неверными только в счёте домашки:
    в попытки и PTS они не пишутся."""
    assignment = _assignment(db, user, homework_id, lock=True)
    if assignment.submitted_at is not None:
        db.rollback()
        raise Conflict("Домашка уже сдана")
    assignment.submitted_at = _now()
    db.commit()
    return my_detail(db, user, homework_id)


# ---------- Админ ----------

def _get(db: Session, homework_id: int) -> Homework:
    hw = db.get(Homework, homework_id)
    if hw is None:
        raise NotFound
    return hw


def _picker_row(task: Task, tried_by: list[int] | None = None) -> dict:
    return {
        "id": task.id, "exam_number": task.exam_code, "statement": task.statement,
        "source": task.source, "source_label": task.source_label, "difficulty": task.difficulty,
        "is_archived": task.is_archived, "tried_by": tried_by or [],
    }


def _tried(db: Session, user_ids: list[int], task_ids: list[int]) -> dict[int, list[int]]:
    """{task_id: [user_id, …]} — кто из учеников уже отвечал на задачу."""
    if not user_ids or not task_ids:
        return {}
    rows = db.execute(
        select(distinct(Attempt.task_id), Attempt.user_id)
        .where(Attempt.user_id.in_(user_ids), Attempt.task_id.in_(task_ids))
        .order_by(Attempt.user_id)
    ).all()
    tried: dict[int, list[int]] = {}
    for task_id, user_id in rows:
        tried.setdefault(task_id, []).append(user_id)
    return tried


def _check_tasks(db: Session, task_ids: list[int]) -> None:
    if len(set(task_ids)) != len(task_ids):
        raise Invalid("Одна и та же задача выбрана дважды")
    found = set(db.scalars(select(Task.id).where(Task.id.in_(task_ids), Task.is_archived.is_(False))))
    missing = [t for t in task_ids if t not in found]
    if missing:
        raise Invalid(f"Нет таких задач (или они в архиве): {', '.join(map(str, missing))}")


def _check_users(db: Session, user_ids: list[int]) -> list[int]:
    unique = list(dict.fromkeys(user_ids))
    found = set(db.scalars(select(User.id).where(User.id.in_(unique))))
    missing = [u for u in unique if u not in found]
    if missing:
        raise Invalid(f"Нет таких учеников: {', '.join(map(str, missing))}")
    return unique


def _set_tasks(db: Session, hw: Homework, task_ids: list[int]) -> None:
    _check_tasks(db, task_ids)
    db.execute(delete(HomeworkTask).where(HomeworkTask.homework_id == hw.id))
    db.flush()
    db.expire(hw, ["tasks"])
    for position, task_id in enumerate(task_ids, start=1):
        db.add(HomeworkTask(homework_id=hw.id, position=position, task_id=task_id))


def _add_students(db: Session, hw: Homework, user_ids: list[int]) -> None:
    have = {a.user_id for a in hw.assignments}
    for user_id in _check_users(db, user_ids):
        if user_id not in have:
            hw.assignments.append(HomeworkAssignment(user_id=user_id))


def admin_list(db: Session) -> list[dict]:
    homeworks = db.scalars(
        select(Homework)
        .options(
            selectinload(Homework.tasks).selectinload(HomeworkTask.task).joinedload(Task.exam_number),
            selectinload(Homework.assignments),
        )
        .order_by(Homework.sent_at.is_not(None), Homework.created_at.desc(), Homework.id.desc())
    ).all()
    return [{
        "id": hw.id, "title": hw.title, "created_at": hw.created_at, "sent_at": hw.sent_at,
        "numbers": _numbers(hw), "tasks_count": len(hw.tasks), "students_count": len(hw.assignments),
        "submitted_count": sum(1 for a in hw.assignments if a.submitted_at is not None),
    } for hw in homeworks]


def admin_detail(db: Session, homework_id: int) -> dict:
    hw = _get(db, homework_id)
    user_ids = [a.user_id for a in hw.assignments]
    tried = _tried(db, user_ids, [item.task_id for item in hw.tasks])
    return {
        "id": hw.id, "title": hw.title, "comment": hw.comment, "created_at": hw.created_at, "sent_at": hw.sent_at,
        "tasks": [_picker_row(item.task, tried.get(item.task_id)) for item in hw.tasks],
        "students": [a.user for a in hw.assignments],
    }


def create(db: Session, admin: User, title: str, comment: str | None, task_ids: list[int], user_ids: list[int]) -> dict:
    hw = Homework(title=title, comment=comment, created_by=admin.id)
    db.add(hw)
    db.flush()
    _set_tasks(db, hw, task_ids)
    _add_students(db, hw, user_ids)
    db.commit()
    db.refresh(hw)
    return admin_detail(db, hw.id)


def update(db: Session, homework_id: int, changes: dict) -> dict:
    hw = _get(db, homework_id)
    if hw.sent_at is not None and ({"task_ids", "user_ids"} & changes.keys()):
        raise Conflict("После отправки можно менять только название и комментарий, а учеников — добавлять")
    if "title" in changes:
        if not changes["title"]:
            raise Invalid("Название не может быть пустым")
        hw.title = changes["title"]
    if "comment" in changes:
        hw.comment = changes["comment"]
    if changes.get("task_ids") is not None:
        _set_tasks(db, hw, changes["task_ids"])
    if changes.get("user_ids") is not None:
        keep = set(_check_users(db, changes["user_ids"]))
        hw.assignments = [a for a in hw.assignments if a.user_id in keep]
        _add_students(db, hw, changes["user_ids"])
    db.commit()
    return admin_detail(db, hw.id)


def send(db: Session, homework_id: int) -> dict:
    hw = _get(db, homework_id)
    if hw.sent_at is not None:
        raise Conflict("Домашка уже отправлена")
    if not hw.tasks:
        raise Invalid("В домашке нет задач")
    if not hw.assignments:
        raise Invalid("Не выбраны ученики")
    hw.sent_at = _now()
    for a in hw.assignments:
        a.assigned_at = hw.sent_at
    db.commit()
    return admin_detail(db, hw.id)


def add_students(db: Session, homework_id: int, user_ids: list[int]) -> dict:
    hw = _get(db, homework_id)
    _add_students(db, hw, user_ids)
    db.commit()
    return admin_detail(db, hw.id)


def remove(db: Session, homework_id: int) -> None:
    """Удалить домашку у всех. Попытки и PTS учеников остаются — это обычные ответы."""
    db.delete(_get(db, homework_id))
    db.commit()


def results(db: Session, homework_id: int) -> dict:
    hw = _get(db, homework_id)
    order = [item.task_id for item in hw.tasks]
    rows = []
    for a in hw.assignments:
        answers = {x.task_id: x for x in a.answers}
        cells = [
            {"answer": x.answer, "is_correct": x.is_correct, "parts": x.parts, "answered_at": x.answered_at}
            if (x := answers.get(task_id)) else None
            for task_id in order
        ]
        answered, correct = _counts(a)
        rows.append({"student": a.user, "submitted_at": a.submitted_at, "answered": answered,
                     "correct": correct, "cells": cells})
    return {
        "id": hw.id, "title": hw.title, "sent_at": hw.sent_at,
        "tasks": [{"position": i.position, "task_id": i.task_id, "exam_number": i.task.exam_code} for i in hw.tasks],
        "rows": rows,
    }


def picker(
    db: Session, number: str | None, q: str, user_ids: list[int], limit: int, offset: int
) -> dict:
    """Банк задач для конструктора: фильтр по номеру и поиск по тексту условия."""
    query = select(Task).join(ExamNumber, ExamNumber.id == Task.exam_number_id).where(Task.is_archived.is_(False))
    if number:
        query = query.where(ExamNumber.code == number)
    if q.strip():
        pattern = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(Task.statement.ilike(pattern, escape="\\"))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    found = db.scalars(query.order_by(ExamNumber.position, Task.id.desc()).limit(limit).offset(offset)).all()
    tried = _tried(db, user_ids, [t.id for t in found])
    return {"total": total, "items": [_picker_row(t, tried.get(t.id)) for t in found]}


def random_tasks(db: Session, number: str, count: int, user_ids: list[int], exclude_task_ids: list[int]) -> list[dict]:
    """Случайные задачи номера, которых ещё нет в домашке и которые никто из учеников не решал."""
    if tasks.get_number(db, number) is None:
        raise Invalid(f"Номер ЕГЭ «{number}» не существует")
    query = (
        select(Task).join(ExamNumber, ExamNumber.id == Task.exam_number_id)
        .where(ExamNumber.code == number, Task.is_archived.is_(False))
    )
    if exclude_task_ids:
        query = query.where(Task.id.not_in(exclude_task_ids))
    if user_ids:
        query = query.where(Task.id.not_in(select(Attempt.task_id).where(Attempt.user_id.in_(user_ids))))
    return [_picker_row(t) for t in db.scalars(query.order_by(func.random()).limit(count))]
