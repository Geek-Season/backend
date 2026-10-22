"""Уведомления: что и когда отправили ученику.

Строка появляется, когда воркер обработал событие. sent_at — когда сообщение
ушло в Telegram; error — почему не ушло (например, ученик заблокировал бота).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.modules.auth import models as _auth_models  # noqa: F401


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Событие, из которого сделано уведомление. Уникально — одно событие = одно уведомление.
    event_id: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(40))  # achievement.granted, exam.finished
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(String(300))
