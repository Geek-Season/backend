"""Таблицы PTS.

    users ── pts, pts_rd, pts_vol, pts_games          общий PTS ученика
    tasks ── rating, rating_rd, rating_vol, rating_games    рейтинг задачи («сложность по факту»)
    user_number_ratings   ученик × номер ЕГЭ: PTS по номеру (для прогноза балла и слабых тем)
    pts_changes           история: каждая засчитанная первая встреча с задачей — было/стало
    users.rank_key, users.calibrated_at   ранг (после входного пробника)
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.exam import models as _exam_models  # noqa: F401
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
    """Одна засчитанная первая встреча с задачей: сколько было и стало у ученика, по номеру и у задачи.

    Источник — попытка на странице номера (attempt_id) или пробник (exam_session_id).
    На пару «ученик, задача» — не больше одной строки: повторные попытки PTS не меняют.
    """

    __tablename__ = "pts_changes"
    __table_args__ = (UniqueConstraint("user_id", "task_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), unique=True)
    exam_session_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_sessions.id", ondelete="SET NULL"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    exam_number_id: Mapped[int] = mapped_column(ForeignKey("exam_numbers.id"))
    score: Mapped[float] = mapped_column(Float)  # 1 — верно, 0 — нет; 19–21 и пробник — доля верных пунктов
    pts_before: Mapped[float] = mapped_column(Float)
    pts_after: Mapped[float] = mapped_column(Float)
    number_pts_before: Mapped[float] = mapped_column(Float)
    number_pts_after: Mapped[float] = mapped_column(Float)
    task_before: Mapped[float] = mapped_column(Float)
    task_after: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)  # время попытки
