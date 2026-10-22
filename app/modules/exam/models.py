"""Пробный экзамен.

Сессия — одно прохождение пробника учеником. Вариант собирается при старте:
по одной случайной задаче на каждый номер ЕГЭ, где есть задачи. Каждая позиция
варианта — строка exam_answers; в ней же хранится ответ ученика.

    users 1 ──── * exam_sessions 1 ──── * exam_answers * ──── 1 tasks
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.tasks.models import Task, TaskPart


class ExamSession(Base):
    __tablename__ = "exam_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Сервер — единственный источник времени: браузер только показывает отсчёт.
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    max_score: Mapped[int] = mapped_column(Integer)
    primary_score: Mapped[int | None] = mapped_column(Integer)

    answers: Mapped[list["ExamAnswer"]] = relationship(
        back_populates="session", order_by="ExamAnswer.position", cascade="all, delete-orphan"
    )


class ExamAnswer(Base):
    __tablename__ = "exam_answers"
    __table_args__ = (UniqueConstraint("session_id", "position"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("exam_sessions.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)  # 1, 2, … — порядок в варианте
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    # Для 19–21: какой именно вопрос задачи стоит на этой позиции. У обычных задач — NULL.
    part_id: Mapped[int | None] = mapped_column(ForeignKey("task_parts.id"))
    points: Mapped[int] = mapped_column(Integer)  # сколько первичных баллов стоит задача

    answer: Mapped[str | None] = mapped_column(String(100))
    is_correct: Mapped[bool | None] = mapped_column(Boolean)  # считается при завершении
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    session: Mapped[ExamSession] = relationship(back_populates="answers")
    task: Mapped[Task] = relationship()
    part: Mapped[TaskPart | None] = relationship()

    @property
    def correct_answer(self) -> str:
        return self.part.correct_answer if self.part else self.task.correct_answer
