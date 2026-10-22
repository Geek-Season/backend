"""Статистика ученика.

Задача считается РЕШЁННОЙ, если хотя бы одна попытка по ней верная.
Задача НЕ ДОРЕШАНА (failed), если попытки были, но ни одной верной.
Архивные задачи в «решено» и «всего» не учитываются.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.progress.models import Attempt
from app.modules.tasks.models import ExamNumber, Task

ACTIVITY_DAYS = 14
RECENT_LIMIT = 10


def record_attempt(db: Session, user_id: int, task_id: int, answer: str, is_correct: bool) -> None:
    db.add(Attempt(user_id=user_id, task_id=task_id, answer=answer, is_correct=is_correct))
    db.commit()


def _solved_task_ids(user_id: int):
    """Подзапрос: id активных задач, которые ученик решил верно."""
    return (
        select(distinct(Attempt.task_id).label("task_id"))
        .join(Task, Task.id == Attempt.task_id)
        .where(Attempt.user_id == user_id, Attempt.is_correct.is_(True), Task.is_archived.is_(False))
    )


def solved_by_number(db: Session, user_id: int) -> dict[int, int]:
    """{exam_number_id: сколько задач номера решено} — для полосок на карточках."""
    solved = _solved_task_ids(user_id).subquery()
    rows = db.execute(
        select(Task.exam_number_id, func.count())
        .join(solved, solved.c.task_id == Task.id)
        .group_by(Task.exam_number_id)
    ).all()
    return dict(rows)


def task_statuses(db: Session, user_id: int, task_ids: list[int]) -> dict[int, str]:
    """{task_id: "solved" | "failed"} для задач, которые ученик уже пробовал."""
    if not task_ids:
        return {}
    rows = db.execute(
        select(Attempt.task_id, func.bool_or(Attempt.is_correct))
        .where(Attempt.user_id == user_id, Attempt.task_id.in_(task_ids))
        .group_by(Attempt.task_id)
    ).all()
    return {task_id: ("solved" if ok else "failed") for task_id, ok in rows}


def _streak(active_days: set[date], today: date) -> int:
    """Сколько дней подряд ученик решал хоть что-то.

    Если сегодня ещё не решал — серия не сгорает, считаем от вчера.
    """
    day = today if today in active_days else today - timedelta(days=1)
    streak = 0
    while day in active_days:
        streak += 1
        day -= timedelta(days=1)
    return streak


def user_stats(db: Session, user_id: int) -> dict:
    tz = settings.timezone
    today = datetime.now(ZoneInfo(tz)).date()

    # --- итоги ---
    total_attempts, correct_attempts = db.execute(
        select(
            func.count(Attempt.id),
            func.coalesce(func.sum(case((Attempt.is_correct, 1), else_=0)), 0),
        ).where(Attempt.user_id == user_id)
    ).one()

    attempted_tasks = db.scalar(
        select(func.count(distinct(Attempt.task_id))).where(Attempt.user_id == user_id)
    )
    solved_tasks = db.scalar(select(func.count()).select_from(_solved_task_ids(user_id).subquery()))
    total_tasks = db.scalar(select(func.count(Task.id)).where(Task.is_archived.is_(False)))

    # --- по дням (в часовом поясе settings.timezone) ---
    local_day = func.date(func.timezone(tz, Attempt.created_at))
    day_rows = db.execute(
        select(
            local_day.label("day"),
            func.count(Attempt.id),
            func.sum(case((Attempt.is_correct, 1), else_=0)),
        )
        .where(Attempt.user_id == user_id)
        .group_by("day")
    ).all()
    per_day = {day: (count, correct) for day, count, correct in day_rows}

    activity = []
    for offset in range(ACTIVITY_DAYS - 1, -1, -1):
        day = today - timedelta(days=offset)
        count, correct = per_day.get(day, (0, 0))
        activity.append({"date": day, "attempts": count, "correct": correct})

    # --- по номерам ЕГЭ ---
    solved_map = solved_by_number(db, user_id)
    attempted_map = dict(db.execute(
        select(Task.exam_number_id, func.count(distinct(Attempt.task_id)))
        .join(Task, Task.id == Attempt.task_id)
        .where(Attempt.user_id == user_id, Task.is_archived.is_(False))
        .group_by(Task.exam_number_id)
    ).all())
    task_counts = dict(db.execute(
        select(Task.exam_number_id, func.count(Task.id))
        .where(Task.is_archived.is_(False))
        .group_by(Task.exam_number_id)
    ).all())
    by_number = [
        {
            "code": n.code,
            "title": n.title,
            "tasks_count": task_counts.get(n.id, 0),
            "attempted": attempted_map.get(n.id, 0),
            "solved": solved_map.get(n.id, 0),
        }
        for n in db.scalars(select(ExamNumber).order_by(ExamNumber.position))
    ]

    # --- последние ответы ---
    recent_rows = db.execute(
        select(Attempt, ExamNumber.code)
        .join(Task, Task.id == Attempt.task_id)
        .join(ExamNumber, ExamNumber.id == Task.exam_number_id)
        .where(Attempt.user_id == user_id)
        .order_by(Attempt.created_at.desc(), Attempt.id.desc())
        .limit(RECENT_LIMIT)
    ).all()
    recent = [
        {
            "task_id": a.task_id,
            "exam_number": code,
            "answer": a.answer,
            "is_correct": a.is_correct,
            "created_at": a.created_at,
        }
        for a, code in recent_rows
    ]

    return {
        "solved_tasks": solved_tasks,
        "attempted_tasks": attempted_tasks,
        "total_tasks": total_tasks,
        "total_attempts": total_attempts,
        "correct_attempts": correct_attempts,
        "accuracy": round(correct_attempts / total_attempts, 3) if total_attempts else None,
        "streak_days": _streak({d for d, (c, _) in per_day.items() if c}, today),
        "active_days": len(per_day),
        "activity": activity,
        "by_number": by_number,
        "recent": recent,
    }
