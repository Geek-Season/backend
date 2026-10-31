"""Выдача достижений.

check_and_grant() не верит содержимому события: он заново считает показатели
ученика по базе и выдаёт всё, что заслужено и ещё не выдано. Поэтому:
- повторная обработка того же события ничего не продублирует;
- пропущенное событие (брокер был недоступен) исправит следующее.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import distinct, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.achievements.catalog import ACHIEVEMENTS
from app.modules.achievements.models import Achievement, UserAchievement
from app.modules.auth.models import User
from app.modules.exam.models import ExamSession
from app.modules.exam.scale import to_test_score
from app.modules.progress import service as progress
from app.modules.progress.models import Attempt
from app.modules.pts.ranks import RANKS, by_key
from app.modules.tasks.models import Task

NUMBER_CLOSED_MIN_TASKS = 3


def seed_catalog(db: Session) -> None:
    """Записать справочник в таблицу achievements (вызывается из app/seed.py)."""
    for position, (code, icon, title, description) in enumerate(ACHIEVEMENTS, start=1):
        db.merge(Achievement(code=code, icon=icon, title=title, description=description, position=position))
    db.commit()


def _metrics(db: Session, user_id: int) -> dict:
    solved = db.scalar(
        select(func.count(distinct(Attempt.task_id)))
        .where(Attempt.user_id == user_id, Attempt.is_correct.is_(True))
    )

    tz = settings.timezone
    days = set(db.scalars(
        select(distinct(func.date(func.timezone(tz, Attempt.created_at)))).where(Attempt.user_id == user_id)
    ))
    today: date = datetime.now(ZoneInfo(tz)).date()

    exams = db.execute(
        select(func.count(ExamSession.id), func.max(ExamSession.primary_score))
        .where(ExamSession.user_id == user_id, ExamSession.finished_at.is_not(None))
    ).one()

    # Номера, где задач ≥ 3 и все решены.
    totals = dict(db.execute(
        select(Task.exam_number_id, func.count(Task.id))
        .where(Task.is_archived.is_(False))
        .group_by(Task.exam_number_id)
    ).all())
    solved_by_number = progress.solved_by_number(db, user_id)
    closed = any(
        total >= NUMBER_CLOSED_MIN_TASKS and solved_by_number.get(number_id, 0) >= total
        for number_id, total in totals.items()
    )

    user = db.get(User, user_id)
    rank = by_key(user.rank_key) if user else None
    return {
        # Высота ранга: 0 — Рекрут (или ранга нет), 6 — Immortal.
        "rank_level": len(RANKS) - 1 - RANKS.index(rank) if rank else 0,
        "solved": solved,
        "streak": progress._streak(days, today),
        "exams": exams[0],
        "best_test": to_test_score(exams[1]) if exams[1] is not None else 0,
        "number_closed": closed,
    }


def _earned(m: dict) -> set[str]:
    rules = {
        "first_solve": m["solved"] >= 1,
        "solved_10": m["solved"] >= 10,
        "solved_50": m["solved"] >= 50,
        "streak_3": m["streak"] >= 3,
        "streak_7": m["streak"] >= 7,
        "first_exam": m["exams"] >= 1,
        "exam_70": m["best_test"] >= 70,
        "number_closed": m["number_closed"],
        **{f"rank_{r.key}": m["rank_level"] >= len(RANKS) - 1 - i for i, r in enumerate(RANKS) if r.key != "recruit"},
    }
    return {code for code, ok in rules.items() if ok}


def check_and_grant(db: Session, user_id: int) -> list[Achievement]:
    """Выдать заслуженные достижения. Возвращает только НОВЫЕ."""
    earned = _earned(_metrics(db, user_id))
    if not earned:
        return []
    # ON CONFLICT DO NOTHING + RETURNING: два воркера одновременно не выдадут одно и то же дважды,
    # а в ответ придут только реально добавленные строки.
    inserted = db.scalars(
        insert(UserAchievement)
        .values([{"user_id": user_id, "achievement_code": code} for code in earned])
        .on_conflict_do_nothing()
        .returning(UserAchievement.achievement_code)
    ).all()
    db.commit()
    if not inserted:
        return []
    return list(db.scalars(
        select(Achievement).where(Achievement.code.in_(inserted)).order_by(Achievement.position)
    ))


def list_for_user(db: Session, user_id: int) -> list[dict]:
    received = dict(db.execute(
        select(UserAchievement.achievement_code, UserAchievement.received_at)
        .where(UserAchievement.user_id == user_id)
    ).all())
    return [
        {
            "code": a.code, "icon": a.icon, "title": a.title, "description": a.description,
            "received_at": received.get(a.code),
        }
        for a in db.scalars(select(Achievement).order_by(Achievement.position))
    ]
