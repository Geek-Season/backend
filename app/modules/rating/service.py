"""Рейтинг в Redis.

Очко = задача, решённая ВПЕРВЫЕ (повторное решение очков не даёт).
Два «таблицы лидеров» — sorted set'ы Redis (ключ → {user_id: очки}):

    rating:all                  — за всё время
    rating:week:2026-W43        — за текущую неделю (по Москве); старые недели удаляются сами

Sorted set хранит участников уже отсортированными по очкам: топ-20 и место
ученика Redis отдаёт сразу, без подсчёта по всей таблице попыток.

Источник правды — таблица attempts в PostgreSQL. Если Redis очистился
(перезапуск без сохранения), рейтинг пересобирается из базы при первом запросе.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.auth.models import User
from app.modules.progress import service as progress

ALL_KEY = "rating:all"
READY_KEY = "rating:ready"  # метка «рейтинг собран»; пропадёт вместе с данными Redis
WEEK_TTL_SECONDS = 21 * 24 * 3600


def _now() -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def week_key(moment: datetime | None = None) -> str:
    year, week, _ = (moment or _now()).isocalendar()
    return f"rating:week:{year}-W{week:02d}"


def _week_start(moment: datetime) -> datetime:
    day = moment.date() - timedelta(days=moment.weekday())
    return datetime(day.year, day.month, day.day, tzinfo=moment.tzinfo)


def add_point(r: Redis, user_id: int, points: int = 1) -> None:
    """Ученик впервые решил задачу (или несколько — например, в пробнике)."""
    if points <= 0:
        return
    key = week_key()
    pipe = r.pipeline()  # обе команды уходят в Redis одним пакетом
    pipe.zincrby(ALL_KEY, points, user_id)
    pipe.zincrby(key, points, user_id)
    pipe.expire(key, WEEK_TTL_SECONDS)
    pipe.execute()


def rebuild(r: Redis, db: Session) -> None:
    """Пересобрать рейтинг из базы."""
    now = _now()
    key = week_key(now)
    all_time = progress.first_solves(db)
    this_week = progress.first_solves(db, since=_week_start(now))

    pipe = r.pipeline()
    pipe.delete(ALL_KEY, key)
    if all_time:
        pipe.zadd(ALL_KEY, all_time)
    if this_week:
        pipe.zadd(key, this_week)
        pipe.expire(key, WEEK_TTL_SECONDS)
    pipe.set(READY_KEY, 1)
    pipe.execute()


def ensure(r: Redis, db: Session) -> None:
    if not r.exists(READY_KEY):
        rebuild(r, db)


def leaderboard(r: Redis, db: Session, period: str, limit: int, user_id: int | None) -> dict:
    ensure(r, db)
    key = ALL_KEY if period == "all" else week_key()

    top = r.zrevrange(key, 0, limit - 1, withscores=True)
    ids = [int(member) for member, _ in top]
    users = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}

    def place_for(score: float) -> int:
        # Место = 1 + сколько участников набрали СТРОГО больше. Равные очки — общее место.
        return r.zcount(key, f"({score}", "+inf") + 1

    rows = []
    for member, score in top:
        user = users.get(int(member))
        if user is None:  # пользователя удалили — пропускаем
            continue
        rows.append({
            "place": place_for(score),
            "user_id": user.id,
            # Только имя на сайте (ник или имя из Telegram): @username сюда не попадает —
            # по нему любой посетитель рейтинга мог бы найти ученика в Telegram.
            "name": user.display_name,
            "has_nickname": user.nickname is not None,  # админу — кнопка «сбросить ник»
            "avatar_url": user.avatar_url,
            "has_custom_avatar": user.avatar_custom,  # админу — «сбросить аватарку»
            "rank": user.rank_info,  # другим виден только ранг — ни PTS, ни прогноз
            "points": int(score),
            "is_me": user.id == user_id,
        })

    me = None
    if user_id is not None:
        score = r.zscore(key, user_id)
        me = {
            "place": place_for(score) if score is not None else None,
            "points": int(score) if score is not None else 0,
        }

    return {"period": period, "participants": r.zcard(key), "top": rows, "me": me}
