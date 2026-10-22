"""Таблицы базы данных для банка заданий.

Каждый класс = одна таблица, каждый атрибут = одна колонка.
Связь простая: у номера ЕГЭ много задач (прототипов), у задачи — один номер.

    exam_numbers 1 ──── * tasks
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class ExamNumber(Base):
    """Номер задания ЕГЭ: одна карточка на странице «Задания»."""

    __tablename__ = "exam_numbers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Как номер пишется на карточке и в адресе: "1", "2", ..., "19-21".
    code: Mapped[str] = mapped_column(String(10), unique=True)
    title: Mapped[str] = mapped_column(String(100))
    # Порядок карточек на странице.
    position: Mapped[int] = mapped_column(Integer, unique=True)

    tasks: Mapped[list["Task"]] = relationship(back_populates="exam_number")


class Task(Base):
    """Задача-прототип внутри номера."""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    exam_number_id: Mapped[int] = mapped_column(ForeignKey("exam_numbers.id"), index=True)

    statement: Mapped[str] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(500))
    correct_answer: Mapped[str] = mapped_column(String(100))
    explanation: Mapped[str] = mapped_column(Text, default="")

    # Откуда задача: demo (демоверсия), official (реальный экзамен), author (авторская).
    source: Mapped[str] = mapped_column(String(20), default="author")
    # Подпись к метке источника, например «Пересдача 08.07.26».
    source_label: Mapped[str | None] = mapped_column(String(100))
    difficulty: Mapped[int] = mapped_column(Integer, default=50)

    # Задачи не удаляем физически: по ним потом будут решения учеников.
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    exam_number: Mapped[ExamNumber] = relationship(back_populates="tasks")

    @property
    def exam_code(self) -> str:
        """Код номера («1», «19-21») — удобно отдавать в API вместо id."""
        return self.exam_number.code
