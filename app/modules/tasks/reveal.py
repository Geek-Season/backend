"""Когда ученику можно открыть правильный ответ и разбор.

Правило: сразу после верного ответа, а после неверных — только когда их набралось
settings.reveal_after_wrong (по умолчанию 3). Раньше ответ и разбор показывались
после первой же ошибки, и ученик не пытался подумать сам.

Где считаются неверные ответы:
- вошедший ученик — по его попыткам в базе (таблица attempts);
- гость — в Redis по адресу и задаче, неделю. Гость может обнулить счётчик, сменив
  адрес, — это нестрашно: у гостя нет ни рейтинга, ни статистики.

Сервер не отдаёт ответ раньше времени нигде: ни в проверке, ни в списке задач,
поэтому подсмотреть его в инструментах браузера нельзя.
"""

import logging

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.progress import service as progress

logger = logging.getLogger(__name__)

GUEST_TTL = 7 * 24 * 3600
SOLVED = 1_000_000  # «решил» в счётчике гостя: разбор открыт навсегда


def _guest_key(client: str, task_id: int) -> str:
    return f"wrong:{client}:{task_id}"


def is_open(solved: bool, wrong: int) -> bool:
    return solved or wrong >= settings.reveal_after_wrong


def attempts_left(solved: bool, wrong: int) -> int:
    return 0 if solved else max(0, settings.reveal_after_wrong - wrong)


# ---------- Вошедший ученик ----------

def user_state(db: Session, user_id: int, task_id: int) -> tuple[bool, int]:
    """(решена ли, неверных ответов) — попытки уже сохранены в базе."""
    return progress.task_progress(db, user_id, [task_id]).get(task_id, (False, 0))


def user_open(db: Session, user_id: int, task_ids: list[int]) -> set[int]:
    """Задачи из списка, у которых ученику открыт разбор."""
    return {tid for tid, (solved, wrong) in progress.task_progress(db, user_id, task_ids).items()
            if is_open(solved, wrong)}


# ---------- Гость ----------

def guest_record(r: Redis, client: str, task_id: int, is_correct: bool) -> tuple[bool, int]:
    """Учесть ответ гостя и вернуть (решена ли, неверных ответов)."""
    key = _guest_key(client, task_id)
    try:
        if is_correct:
            r.set(key, SOLVED, ex=GUEST_TTL)
            return True, 0
        wrong = r.incr(key)
        r.expire(key, GUEST_TTL)
    except RedisError:
        logger.warning("Redis недоступен: счётчик неверных ответов гостя не сохранён")
        return False, 0
    return wrong >= SOLVED, wrong


def guest_state(r: Redis, client: str, task_id: int) -> tuple[bool, int]:
    try:
        value = int(r.get(_guest_key(client, task_id)) or 0)
    except RedisError:
        return False, 0
    return value >= SOLVED, value


def guest_open(r: Redis, client: str, task_ids: list[int]) -> set[int]:
    if not task_ids:
        return set()
    try:
        values = r.mget([_guest_key(client, tid) for tid in task_ids])
    except RedisError:
        return set()
    return {tid for tid, v in zip(task_ids, values) if v and is_open(int(v) >= SOLVED, int(v))}
