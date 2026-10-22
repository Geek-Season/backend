"""Назначить или снять администратора.

    python -m app.make_admin 123456789          — по Telegram ID
    python -m app.make_admin @username          — по @username (человек должен хоть раз войти)
    python -m app.make_admin @username --remove — снять роль

Telegram ID можно узнать у бота @userinfobot. Другой способ — указать ID
в .env (ADMIN_TELEGRAM_IDS), тогда роль выдаётся при входе.
"""

import sys

from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.modules.auth.models import ROLE_ADMIN, ROLE_STUDENT, User


def set_role(who: str, role: str) -> str:
    with SessionLocal() as db:
        if who.startswith("@"):
            user = db.scalar(select(User).where(func.lower(User.username) == who[1:].lower()))
        elif who.isdigit():
            user = db.scalar(select(User).where(User.telegram_id == int(who)))
        else:
            return f"Не понимаю «{who}»: укажите Telegram ID или @username"
        if user is None:
            return f"Пользователь {who} не найден — пусть сначала войдёт на сайт"
        user.role = role
        db.commit()
        return f"{user.first_name} ({who}) теперь {role}"


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    print(set_role(args[0], ROLE_STUDENT if "--remove" in args else ROLE_ADMIN))
