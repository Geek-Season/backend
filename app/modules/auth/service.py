"""Логика входа через Telegram.

Эти функции вызывают и сайт (через router.py), и бот (app/bot).
Временные данные хранятся в Redis и исчезают сами по истечении срока:

    login:qr:{ключ}        "pending" → "confirmed:{user_id}" или "declined"
    login:code:{код}       user_id — код из бота
    login:user_code:{id}   последний выданный пользователю код (чтобы старый сгорел)
    login:attempts:{адрес} сколько раз за минуту вводили код

ВХОД ПО QR
  1. Сайт: start_qr_login() → ключ. Ключ зашит в QR-ссылку на бота.
  2. Бот: человек нажал «Да» → confirm_qr_login(ключ, профиль Telegram).
  3. Сайт раз в 2 секунды: check_qr_login(ключ). Когда "confirmed" —
     ключ удаляется (одноразовый) и сайт получает токен входа.

ВХОД ПО КОДУ
  1. Бот: /login → issue_code(профиль) → 6 цифр.
  2. Сайт: redeem_code(код) → user_id, код сразу удаляется.
"""

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.auth.models import ROLE_ADMIN, User

PENDING = "pending"
DECLINED = "declined"
CONFIRMED_PREFIX = "confirmed:"


class TooManyAttempts(Exception):
    """Слишком много попыток ввести код — похоже на перебор."""


@dataclass
class TelegramProfile:
    """То, что бот знает о человеке из Telegram."""

    telegram_id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None


def _qr_key(token: str) -> str:
    return f"login:qr:{token}"


def _code_key(code: str) -> str:
    return f"login:code:{code}"


# ---------- Пользователи ----------

def upsert_user(db: Session, profile: TelegramProfile) -> User:
    """Находит пользователя по telegram_id или создаёт нового. Обновляет имя."""
    user = db.scalar(select(User).where(User.telegram_id == profile.telegram_id))
    if user is None:
        user = User(telegram_id=profile.telegram_id, first_name=profile.first_name)
        db.add(user)
    user.first_name = profile.first_name
    user.last_name = profile.last_name
    user.username = profile.username
    user.last_login_at = datetime.now(UTC)
    if profile.telegram_id in settings.admin_ids:
        user.role = ROLE_ADMIN  # админов из .env повышаем; понижать вручную — make_admin --remove
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


# ---------- Вход по QR ----------

def start_qr_login(r: Redis) -> str:
    # 24 случайных байта → 32 символа [A-Za-z0-9_-]: подходит для ссылки t.me/...?start=
    token = secrets.token_urlsafe(24)
    r.set(_qr_key(token), PENDING, ex=settings.login_ttl_seconds)
    return token


def is_qr_pending(r: Redis, token: str) -> bool:
    return r.get(_qr_key(token)) == PENDING


def confirm_qr_login(r: Redis, db: Session, token: str, profile: TelegramProfile) -> bool:
    """Бот: человек нажал «Да». False — если ключ устарел или уже обработан."""
    if not is_qr_pending(r, token):
        return False
    user = upsert_user(db, profile)
    # xx=True — записать, только если ключ ещё существует; keepttl — не продлевать срок.
    return bool(r.set(_qr_key(token), f"{CONFIRMED_PREFIX}{user.id}", xx=True, keepttl=True))


def decline_qr_login(r: Redis, token: str) -> bool:
    """Бот: человек нажал «Нет»."""
    if not is_qr_pending(r, token):
        return False
    return bool(r.set(_qr_key(token), DECLINED, xx=True, keepttl=True))


def check_qr_login(r: Redis, token: str) -> tuple[str, int | None]:
    """Сайт: проверить ключ. Возвращает (статус, user_id или None).

    Подтверждённый ключ удаляется при первой проверке: по одному QR
    может войти только один браузер.
    """
    value = r.get(_qr_key(token))
    if value is None:
        return "expired", None
    if value.startswith(CONFIRMED_PREFIX):
        # delete вернёт 1 только тому, кто удалил первым — второй запрос получит expired.
        if r.delete(_qr_key(token)) == 1:
            return "confirmed", int(value.removeprefix(CONFIRMED_PREFIX))
        return "expired", None
    return value, None  # pending или declined


# ---------- Вход по коду ----------

def issue_code(r: Redis, db: Session, profile: TelegramProfile) -> str:
    """Бот: /login → новый 6-значный код. Предыдущий код этого человека сгорает."""
    user = upsert_user(db, profile)

    old_code = r.get(f"login:user_code:{user.id}")
    if old_code:
        r.delete(_code_key(old_code))

    for _ in range(20):
        code = f"{secrets.randbelow(10**6):06d}"
        # nx=True — записать, только если такого кода сейчас ни у кого нет.
        if r.set(_code_key(code), user.id, ex=settings.login_ttl_seconds, nx=True):
            r.set(f"login:user_code:{user.id}", code, ex=settings.login_ttl_seconds)
            return code
    raise RuntimeError("Не удалось подобрать свободный код")


def redeem_code(r: Redis, code: str, client: str) -> int | None:
    """Сайт: обменять код на user_id. None — код неверный или устарел.

    client — адрес того, кто вводит код: для ограничения числа попыток.
    """
    attempts_key = f"login:attempts:{client}"
    attempts = r.incr(attempts_key)
    if attempts == 1:
        r.expire(attempts_key, 60)
    if attempts > settings.code_attempts_per_minute:
        raise TooManyAttempts

    # getdel — прочитать и сразу удалить одной командой: код одноразовый.
    user_id = r.getdel(_code_key(code))
    if user_id is None:
        return None
    r.delete(f"login:user_code:{user_id}")
    return int(user_id)
