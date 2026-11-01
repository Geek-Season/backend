"""Таблица лидеров по PTS.

Три таблицы:

    all     — за всё время: по общему PTS
    week    — эта неделя (с понедельника, по Москве): кто больше вырос — сумма изменений PTS
              за ответы и пробники с понедельника; входной пробник и правки админа не считаются
              (там PTS выставляется, а не набирается)
    number  — один номер ЕГЭ: по PTS в этом номере

В таблицах только ученики с рангом (после входного пробника) и без блокировки. Другим виден
только ранг: сам PTS (и PTS по номеру) получает лишь его владелец — в поле me.
Прирост за неделю виден всем: он не раскрывает, сколько PTS у человека всего.

Immortal #N — место среди Immortal по общему PTS (как в Доте); показывается во всех таблицах.

Источник — PostgreSQL (users, user_number_ratings, pts_changes); учеников немного, поэтому
таблица считается запросом на каждый вызов, без отдельного хранилища.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.auth.models import User
from app.modules.exam.models import ExamSession
from app.modules.pts import ranks
from app.modules.pts import service as pts
from app.modules.pts.models import PtsChange, UserNumberRating
from app.modules.tasks.models import ExamNumber

IMMORTAL = "immortal"


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def week_start(moment: datetime | None = None) -> datetime:
    moment = moment or _now()
    day = moment.date() - timedelta(days=moment.weekday())
    return datetime(day.year, day.month, day.day, tzinfo=moment.tzinfo)


class UnknownNumber(Exception):
    pass


def _ranked():
    """Кто участвует: есть ранг, не заблокирован."""
    return and_(User.rank_key.is_not(None), User.blocked_at.is_(None))


def immortal_places(db: Session) -> dict[int, int]:
    """{user_id: место среди Immortal по общему PTS}."""
    ids = db.scalars(
        select(User.id).where(_ranked(), User.rank_key == IMMORTAL).order_by(User.pts.desc(), User.id)
    ).all()
    return {uid: i + 1 for i, uid in enumerate(ids)}


@dataclass
class Entry:
    user: User
    score: float  # PTS, PTS по номеру или прирост за неделю


def _board(db: Session, period: str, number_id: int | None) -> list[Entry]:
    """Все участники таблицы, лучшие сверху."""
    if number_id is not None:
        rows = db.execute(
            select(User, UserNumberRating.pts)
            .join(UserNumberRating, UserNumberRating.user_id == User.id)
            .where(_ranked(), UserNumberRating.exam_number_id == number_id)
            .order_by(UserNumberRating.pts.desc(), User.id)
        ).all()
        return [Entry(user, score) for user, score in rows]

    if period == "week":
        since = week_start()
        # Входной пробник ученика — тот, что завершён в момент калибровки: его не считаем.
        entrance = and_(ExamSession.id == PtsChange.exam_session_id, ExamSession.finished_at == User.calibrated_at)
        growth = func.sum(PtsChange.pts_after - PtsChange.pts_before)
        rows = db.execute(
            select(User, growth)
            .join(PtsChange, PtsChange.user_id == User.id)
            .outerjoin(ExamSession, entrance)
            .where(_ranked(), PtsChange.created_at >= since, ExamSession.id.is_(None))
            .group_by(User.id)
            .having(func.round(growth) >= 1)
            .order_by(growth.desc(), User.id)
        ).all()
        return [Entry(user, score) for user, score in rows]

    users = db.scalars(select(User).where(_ranked()).order_by(User.pts.desc(), User.id)).all()
    return [Entry(user, user.pts) for user in users]


def _places(entries: list[Entry]) -> list[int]:
    """Места: равный (до целого) счёт — общее место."""
    places, previous = [], None
    for i, entry in enumerate(entries):
        score = round(entry.score)
        places.append(places[-1] if previous == score else i + 1)
        previous = score
    return places


def _next_rank_hint(db: Session, user: User) -> dict | None:
    """Сколько первичных баллов прогноза не хватает до следующего ранга (null — уже Immortal)."""
    rank = ranks.by_key(user.rank_key)
    up = ranks.next_up(rank) if rank else None
    if up is None:
        return None
    forecast = pts.user_forecast(db, user)
    need = max(1, math.ceil(up.min_primary - 0.5 - forecast.primary_exact))
    return {"rank": {"key": up.key, "title": up.title, "short": up.short}, "primary_needed": need}


def leaderboard(db: Session, period: str = "all", number: str | None = None, rank: str | None = None,
                limit: int = 20, me: User | None = None) -> dict:
    number_id = None
    if number is not None:
        number_id = db.scalar(select(ExamNumber.id).where(ExamNumber.code == number))
        if number_id is None:
            raise UnknownNumber

    entries = _board(db, period, number_id)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.user.rank_key] = counts.get(entry.user.rank_key, 0) + 1
    if rank is not None:
        entries = [e for e in entries if e.user.rank_key == rank]
    places = _places(entries)
    immortals = immortal_places(db)
    week = period == "week" and number_id is None

    def row(entry: Entry, place: int) -> dict:
        user = entry.user
        return {
            "place": place,
            "user_id": user.id,
            # Только имя на сайте (ник или имя из Telegram): @username сюда не попадает —
            # по нему любой посетитель рейтинга мог бы найти ученика в Telegram.
            "name": user.display_name,
            "has_nickname": user.nickname is not None,
            "avatar_url": user.avatar_url,
            "has_custom_avatar": user.avatar_custom,
            "rank": user.rank_info,
            "immortal_place": immortals.get(user.id),
            "growth": round(entry.score) if week else None,  # прирост за неделю виден всем
            "is_me": me is not None and user.id == me.id,
        }

    top = [row(e, p) for e, p in zip(entries[:limit], places[:limit])]

    mine = None
    if me is not None:
        index = next((i for i, e in enumerate(entries) if e.user.id == me.id), None)
        ranked = me.rank_key is not None and me.blocked_at is None
        own_pts = growth = None  # своё — только себе
        if ranked and week:
            growth = round(entries[index].score) if index is not None else 0
        elif ranked and number_id is not None:
            value = db.scalar(select(UserNumberRating.pts).where(
                UserNumberRating.user_id == me.id, UserNumberRating.exam_number_id == number_id))
            own_pts = round(value) if value is not None else None
        elif ranked:
            own_pts = round(me.pts)
        mine = {
            "ranked": ranked,
            "place": places[index] if index is not None else None,
            "pts": own_pts,
            "growth": growth,
            "row": row(entries[index], places[index]) if index is not None and index >= limit else None,
            "next_rank": _next_rank_hint(db, me) if ranked else None,
        }

    return {
        "period": period,
        "number": number,
        "rank": rank,
        "participants": sum(counts.values()),
        "counts": counts,
        "top": top,
        "me": mine,
    }


# ---------- Карточка ученика (по клику на строку рейтинга) ----------

def place_of(db: Session, user: User) -> int | None:
    """Место в общей таблице (по PTS); None — ученика в ней нет (нет ранга или заблокирован)."""
    if user.rank_key is None or user.blocked_at is not None:
        return None
    entries = _board(db, "all", None)
    places = _places(entries)
    return next((p for e, p in zip(entries, places) if e.user.id == user.id), None)


def card(db: Session, user: User) -> dict:
    """Что видят все: имя, аватарка, ранг и место, «занимается с», решено, серия, достижения,
    календарь активности. Ни PTS, ни прогноза, ни пробников, ни @username."""
    from app.modules.achievements import service as achievements
    from app.modules.progress import service as progress

    got = [a for a in achievements.list_for_user(db, user.id) if a["received_at"]]
    total = db.scalar(select(func.count()).select_from(achievements.Achievement))
    calendar = progress.calendar(db, user.id)
    return {
        "user_id": user.id,
        "name": user.display_name,
        "avatar_url": user.avatar_url,
        "rank": user.rank_info,
        "immortal_place": immortal_places(db).get(user.id) if user.rank_key == IMMORTAL else None,
        "place": place_of(db, user),
        "since": user.created_at,
        "solved": progress.solved_count(db, user.id),
        "streak": calendar["streak"],
        "achievements": [{"code": a["code"], "icon": a["icon"], "title": a["title"]} for a in got],
        "achievements_total": total,
        "calendar": calendar,
    }
