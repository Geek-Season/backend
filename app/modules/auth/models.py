"""Таблица пользователей.

Пароля нет: личность подтверждает Telegram. Ключ пользователя — его telegram_id.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


ROLE_STUDENT = "student"
ROLE_ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ID в Telegram не меняется, даже если человек сменил имя или @username.
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str | None] = mapped_column(String(100))
    username: Mapped[str | None] = mapped_column(String(64))
    # student — ученик; admin — может управлять банком задач.
    role: Mapped[str] = mapped_column(String(20), default="student", server_default=text("'student'"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN
