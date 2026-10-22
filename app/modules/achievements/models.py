"""Достижения.

achievements       — справочник: что можно получить (заполняется из app/modules/achievements/catalog.py)
user_achievements  — что получил ученик и когда. Пара (ученик, достижение) уникальна —
                     одно достижение нельзя получить дважды, даже если воркер обработал
                     событие повторно.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.auth import models as _auth_models  # noqa: F401


class Achievement(Base):
    __tablename__ = "achievements"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(300))
    icon: Mapped[str] = mapped_column(String(10))
    position: Mapped[int] = mapped_column(Integer)


class UserAchievement(Base):
    __tablename__ = "user_achievements"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    achievement_code: Mapped[str] = mapped_column(ForeignKey("achievements.code"), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
