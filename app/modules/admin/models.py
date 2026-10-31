"""Журнал действий администраторов.

Каждое изменение ученика из админки — строка: кто, кому, что, было/стало.
Действия с PTS (pts.set, pts.rank, pts.recalibrate, stats.reset) — ещё и события
для пересчёта PTS с нуля (app/modules/pts/service.recompute_all): без них ручная правка
пропала бы при следующем запуске сервера.
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.auth import models as _auth_models  # noqa: F401


class AdminAction(Base):
    __tablename__ = "admin_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Кто сделал. SET NULL — запись в журнале остаётся, даже если админа удалят.
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # С кем сделали.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    action: Mapped[str] = mapped_column(String(40))
    # Параметры и «было/стало», например {"before": {...}, "after": {...}, "pts": 1500}.
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
