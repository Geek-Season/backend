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
CALENDAR_DAYS = 365  # календарь активности в профиле и карточке — за последний год
RECENT_LIMIT = 10


WEAK_LIMIT = 5
WEAK_MIN_ATTEMPTS = 3      # с какого числа ответов судить о точности по номеру
WEAK_ACCURACY = 0.6        # ниже этой точности номер считается слабым


def record_attempt(
    db: Session, user_id: int, task_id: int, answer: str, is_correct: bool, commit: bool = True
) -> bool:
    """Сохраняет ответ. Возвращает True, если задача решена ВПЕРВЫЕ (нужно рейтингу)."""
    first_solve = is_correct and not db.scalar(
        select(func.count(Attempt.id)).where(
            Attempt.user_id == user_id, Attempt.task_id == task_id, Attempt.is_correct.is_(True)
        )
    )
    db.add(Attempt(user_id=user_id, task_id=task_id, answer=answer, is_correct=is_correct))
    if commit:
        db.commit()
    else:
        db.flush()  # чтобы следующая проверка «впервые?» в той же транзакции видела эту попытку
    return first_solve


def first_solves(db: Session, since: datetime | None = None) -> dict[int, int]:
    """{user_id: сколько задач решено впервые} (с даты since, если задана).

    Из этого пересобирается рейтинг, если в Redis его нет.
    """
    first = (
        select(Attempt.user_id, Attempt.task_id, func.min(Attempt.created_at).label("at"))
        .where(Attempt.is_correct.is_(True))
        .group_by(Attempt.user_id, Attempt.task_id)
        .subquery()
    )
    query = select(first.c.user_id, func.count()).group_by(first.c.user_id)
    if since is not None:
        query = query.where(first.c.at >= since)
    return dict(db.execute(query).all())


def weak_numbers(db: Session, user_id: int) -> list[dict]:
    """Номера, которые стоит повторить.

    Номер слабый, если в нём есть задача, которую ученик пробовал, но так и не решил,
    или если ответов по номеру ≥ 3, а точность ниже 60 %.
    Сначала — самые проблемные (ниже точность, больше нерешённых).
    """
    per_task = (
        select(
            Task.exam_number_id.label("number_id"),
            Attempt.task_id,
            func.count(Attempt.id).label("attempts"),
            func.sum(case((Attempt.is_correct, 1), else_=0)).label("correct"),
        )
        .join(Task, Task.id == Attempt.task_id)
        .where(Attempt.user_id == user_id, Task.is_archived.is_(False))
        .group_by(Task.exam_number_id, Attempt.task_id)
        .subquery()
    )
    rows = db.execute(
        select(
            ExamNumber.code,
            ExamNumber.title,
            func.sum(per_task.c.attempts),
            func.sum(per_task.c.correct),
            func.sum(case((per_task.c.correct == 0, 1), else_=0)),
        )
        .join(ExamNumber, ExamNumber.id == per_task.c.number_id)
        .group_by(ExamNumber.code, ExamNumber.title, ExamNumber.position)
        .order_by(ExamNumber.position)
    ).all()

    weak = []
    for code, title, attempts, correct, failed in rows:
        accuracy = correct / attempts
        if failed > 0 or (attempts >= WEAK_MIN_ATTEMPTS and accuracy < WEAK_ACCURACY):
            weak.append({
                "code": code, "title": title, "attempts": attempts, "correct": correct,
                "accuracy": round(accuracy, 3), "unsolved_tasks": failed,
            })
    weak.sort(key=lambda w: (w["accuracy"], -w["unsolved_tasks"]))
    return weak[:WEAK_LIMIT]


def _solved_task_ids(user_id: int):
    """Подзапрос: id активных задач, которые ученик решил верно."""
    return (
        select(distinct(Attempt.task_id).label("task_id"))
        .join(Task, Task.id == Attempt.task_id)
        .where(Attempt.user_id == user_id, Attempt.is_correct.is_(True), Task.is_archived.is_(False))
    )


def solved_task_ids(user_id: int):
    """Подзапрос «id задач, решённых учеником» — для фильтра на странице номера."""
    return _solved_task_ids(user_id)


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


def task_progress(db: Session, user_id: int, task_ids: list[int]) -> dict[int, tuple[bool, int]]:
    """{task_id: (решена ли, сколько неверных ответов)} для задач, которые ученик пробовал."""
    if not task_ids:
        return {}
    rows = db.execute(
        select(
            Attempt.task_id,
            func.bool_or(Attempt.is_correct),
            func.sum(case((Attempt.is_correct, 0), else_=1)),
        )
        .where(Attempt.user_id == user_id, Attempt.task_id.in_(task_ids))
        .group_by(Attempt.task_id)
    ).all()
    return {task_id: (bool(ok), int(wrong or 0)) for task_id, ok, wrong in rows}


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


def _today() -> date:
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _per_day(db: Session, user_id: int, since: date | None = None) -> dict[date, tuple[int, int]]:
    """{день: (ответов, верных)} в часовом поясе settings.timezone. Ответы пробника — тоже здесь:
    при завершении пробника каждый ответ записывается попыткой."""
    local_day = func.date(func.timezone(settings.timezone, Attempt.created_at))
    query = (
        select(local_day.label("day"), func.count(Attempt.id), func.sum(case((Attempt.is_correct, 1), else_=0)))
        .where(Attempt.user_id == user_id)
        .group_by("day")
    )
    if since is not None:
        query = query.having(local_day >= since)
    return {day: (count, int(correct or 0)) for day, count, correct in db.execute(query).all()}


def _best_streak(days: set[date]) -> int:
    best = run = 0
    previous = None
    for day in sorted(days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        best = max(best, run)
        previous = day
    return best


def calendar(db: Session, user_id: int) -> dict:
    """Календарь активности за последний год (как в LeetCode/GitHub): только дни с ответами."""
    today = _today()
    start = today - timedelta(days=CALENDAR_DAYS - 1)
    per_day = _per_day(db, user_id, since=start)
    active = {d for d, (count, _) in per_day.items() if count}
    return {
        "start": start,
        "end": today,
        "days": [{"date": d, "attempts": c, "correct": ok} for d, (c, ok) in sorted(per_day.items()) if c],
        "total": sum(c for c, _ in per_day.values()),
        "active_days": len(active),
        "best_streak": _best_streak(active),
        "streak": _streak(active, today),
    }


def solved_count(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(_solved_task_ids(user_id).subquery()))


def user_stats(db: Session, user_id: int) -> dict:
    today = _today()

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

    per_day = _per_day(db, user_id)

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
        "calendar": calendar(db, user_id),
        "by_number": by_number,
        "weak_numbers": weak_numbers(db, user_id),
        "recent": recent,
    }
