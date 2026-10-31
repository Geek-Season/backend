"""Таблица пользователей.

Пароля нет: личность подтверждает Telegram. Ключ пользователя — его telegram_id.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, func, text
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
    # Ник на сайте (задаёт сам ученик в профиле). None — показываем имя из Telegram.
    nickname: Mapped[str | None] = mapped_column(String(20))
    # Аватарка: имя файла в uploads/avatars. По умолчанию — фото из Telegram (обновляется
    # при входе); avatar_custom — ученик загрузил свою, её фото из Telegram не трогает.
    avatar_file: Mapped[str | None] = mapped_column(String(64))
    avatar_custom: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Какое фото из Telegram сейчас стоит (file_unique_id) — чтобы не скачивать его заново.
    telegram_photo_uid: Mapped[str | None] = mapped_column(String(64))
    # Пол: m, f или None (не указан). Нигде не показывается — на будущее (обращения на сайте).
    gender: Mapped[str | None] = mapped_column(String(1))
    # PTS (app/modules/pts): рейтинг, уверенность (RD), волатильность, сколько ответов засчитано.
    pts: Mapped[float] = mapped_column(Float, default=1000.0, server_default=text("1000"))
    pts_rd: Mapped[float] = mapped_column(Float, default=200.0, server_default=text("200"))
    pts_vol: Mapped[float] = mapped_column(Float, default=0.06, server_default=text("0.06"))
    pts_games: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # Ранг (app/modules/pts/ranks.py): ключ вроде «hero»; None — ещё не было входного пробника.
    rank_key: Mapped[str | None] = mapped_column(String(20))
    calibrated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # student — ученик; admin — может управлять банком задач.
    role: Mapped[str] = mapped_column(String(20), default="student", server_default=text("'student'"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def display_name(self) -> str:
        """Как ученика зовут на сайте: ник, а если его нет — имя из Telegram."""
        return self.nickname or self.first_name

    @property
    def rank_info(self) -> dict | None:
        """Ранг для API: {key, title, short} или None (до входного пробника)."""
        from app.modules.pts.ranks import by_key  # здесь, чтобы модели не зависели от модуля PTS при импорте

        rank = by_key(self.rank_key)
        return {"key": rank.key, "title": rank.title, "short": rank.short} if rank else None

    @property
    def avatar_url(self) -> str | None:
        """Адрес аватарки в API (сайт добавляет к нему адрес сервера). None — показать букву."""
        return f"/avatars/{self.avatar_file}" if self.avatar_file else None

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN
