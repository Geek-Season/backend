"""Домашки: админ собирает набор задач банка и отправляет его выбранным ученикам.

    homeworks 1 ──── * homework_tasks * ──── 1 tasks
              1 ──── * homework_assignments * ──── 1 users
                                            1 ──── * homework_answers

Домашка без sent_at — черновик: ученики её не видят. На каждую задачу у ученика
одна попытка — это гарантирует первичный ключ (assignment_id, task_id) в homework_answers.
Сама попытка, как и на странице номера, пишется ещё и в attempts (статистика, PTS).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.modules.tasks.answers import MAX_ANSWER_LENGTH
from app.modules.auth import models as _auth_models  # noqa: F401
from app.modules.auth.models import User
from app.modules.tasks.models import Task


class Homework(Base):
    __tablename__ = "homeworks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # NULL — черновик. После отправки задачи домашки уже не меняются.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tasks: Mapped[list["HomeworkTask"]] = relationship(
        back_populates="homework", order_by="HomeworkTask.position", cascade="all, delete-orphan"
    )
    assignments: Mapped[list["HomeworkAssignment"]] = relationship(
        back_populates="homework", order_by="HomeworkAssignment.id", cascade="all, delete-orphan"
    )


class HomeworkTask(Base):
    """Задача на своём месте в домашке. Задача 19–21 — одна позиция (три части отвечаются вместе)."""

    __tablename__ = "homework_tasks"
    __table_args__ = (UniqueConstraint("homework_id", "task_id"),)

    homework_id: Mapped[int] = mapped_column(ForeignKey("homeworks.id", ondelete="CASCADE"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)  # 1, 2, …
    # RESTRICT: задачу из домашки нельзя удалить из базы (в архив — можно, она останется в домашке).
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), index=True)

    homework: Mapped[Homework] = relationship(back_populates="tasks")
    task: Mapped[Task] = relationship()


class HomeworkAssignment(Base):
    """Домашка у конкретного ученика."""

    __tablename__ = "homework_assignments"
    __table_args__ = (UniqueConstraint("homework_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    homework_id: Mapped[int] = mapped_column(ForeignKey("homeworks.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # После сдачи ответить уже нельзя, зато открываются разборы.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    homework: Mapped[Homework] = relationship(back_populates="assignments")
    user: Mapped[User] = relationship()
    answers: Mapped[list["HomeworkAnswer"]] = relationship(cascade="all, delete-orphan")


class HomeworkAnswer(Base):
    """Единственный ответ ученика на задачу домашки."""

    __tablename__ = "homework_answers"

    assignment_id: Mapped[int] = mapped_column(
        ForeignKey("homework_assignments.id", ondelete="CASCADE"), primary_key=True
    )
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="RESTRICT"), primary_key=True)
    answer: Mapped[str] = mapped_column(String(MAX_ANSWER_LENGTH))
    is_correct: Mapped[bool] = mapped_column(Boolean)
    # Только у 19–21: [{"number": "19", "answer": "29", "is_correct": true}, …]
    parts: Mapped[list | None] = mapped_column(JSONB)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
