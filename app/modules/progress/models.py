"""Попытки решения: каждый ответ вошедшего ученика — одна строка.

Из этой таблицы считается вся статистика: что решено, точность,
серия дней, активность по дням.

    users 1 ──── * attempts * ──── 1 tasks
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

# Таблицы, на которые ссылаются внешние ключи, должны быть известны SQLAlchemy —
# импортируем их явно, чтобы модель работала и в отдельных скриптах.
from app.modules.auth import models as _auth_models  # noqa: E402,F401
from app.modules.tasks import models as _tasks_models  # noqa: E402,F401


class Attempt(Base):
    __tablename__ = "attempts"
    # Почти все запросы статистики идут по «попытки этого ученика» —
    # индекс по (user_id, task_id) делает их быстрыми.
    __table_args__ = (Index("ix_attempts_user_task", "user_id", "task_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))

    answer: Mapped[str] = mapped_column(String(100))
    is_correct: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
