"""Таблицы PTS.

    users ── pts, pts_rd, pts_vol, pts_games          общий PTS ученика
    tasks ── rating, rating_rd, rating_vol, rating_games    рейтинг задачи («сложность по факту»)
    user_number_ratings   ученик × номер ЕГЭ: PTS по номеру (для прогноза балла и слабых тем)
    pts_changes           история: каждая засчитанная первая попытка — было/стало
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.progress import models as _progress_models  # noqa: F401
from app.modules.tasks import models as _tasks_models  # noqa: F401


class UserNumberRating(Base):
    __tablename__ = "user_number_ratings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    exam_number_id: Mapped[int] = mapped_column(ForeignKey("exam_numbers.id"), primary_key=True)
    pts: Mapped[float] = mapped_column(Float)
    rd: Mapped[float] = mapped_column(Float)
    vol: Mapped[float] = mapped_column(Float)
    games: Mapped[int] = mapped_column(Integer, default=0)


class PtsChange(Base):
    """Одна засчитанная первая попытка: сколько было и стало у ученика, по номеру и у задачи."""

    __tablename__ = "pts_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    exam_number_id: Mapped[int] = mapped_column(ForeignKey("exam_numbers.id"))
    score: Mapped[float] = mapped_column(Float)  # 1 — верно, 0 — нет, у 19–21 — доля верных частей
    pts_before: Mapped[float] = mapped_column(Float)
    pts_after: Mapped[float] = mapped_column(Float)
    number_pts_before: Mapped[float] = mapped_column(Float)
    number_pts_after: Mapped[float] = mapped_column(Float)
    task_before: Mapped[float] = mapped_column(Float)
    task_after: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # время попытки
