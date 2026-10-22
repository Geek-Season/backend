"""Что API принимает и отдаёт (контракты).

Pydantic-схемы описывают данные «на границе» сайта и сервера.
Это не таблицы: таблицы описаны в models.py.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["demo", "official", "author"]
Sort = Literal["new", "old", "easy", "hard"]


# ---------- Номера ЕГЭ ----------

class ExamNumberRead(BaseModel):
    """Карточка на странице «Задания»."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    title: str
    tasks_count: int
    solved: int = Field(default=0, description="Сколько задач номера решил вошедший ученик (0, если не вошёл)")


# ---------- Задачи ----------

class TaskRead(BaseModel):
    """Задача для ученика: БЕЗ правильного ответа и разбора."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    exam_number: str = Field(
        validation_alias="exam_code",
        description="Код номера ЕГЭ, например «1» или «19-21»",
    )
    statement: str
    image_url: str | None
    source: Source
    source_label: str | None
    difficulty: int
    status: Literal["solved", "failed"] | None = Field(
        default=None,
        description="Для вошедшего ученика: solved — решена, failed — пробовал, но неверно",
    )


class TaskPage(BaseModel):
    """Страница прототипов одного номера."""

    number: ExamNumberRead
    total: int = Field(description="Сколько задач подходит под фильтр всего")
    items: list[TaskRead]


class TaskAdminRead(TaskRead):
    """Задача для автора: с ответом и разбором."""

    correct_answer: str
    explanation: str
    created_at: datetime


class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    exam_number: str = Field(examples=["1"])
    statement: str = Field(min_length=1)
    image_url: str | None = Field(default=None, max_length=500)
    correct_answer: str = Field(min_length=1, max_length=100, examples=["42"])
    explanation: str = ""
    source: Source = "author"
    source_label: str | None = Field(default=None, max_length=100)
    difficulty: int = Field(default=50, ge=0, le=100)


class TaskUpdate(BaseModel):
    """Все поля необязательны: меняем только присланные."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    exam_number: str | None = None
    statement: str | None = Field(default=None, min_length=1)
    image_url: str | None = Field(default=None, max_length=500)
    correct_answer: str | None = Field(default=None, min_length=1, max_length=100)
    explanation: str | None = None
    source: Source | None = None
    source_label: str | None = Field(default=None, max_length=100)
    difficulty: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def forbid_null_for_required(self):
        # Поле можно не присылать, но нельзя прислать null для обязательного поля:
        # иначе база откажется сохранять пустое условие или ответ.
        required = {"exam_number", "statement", "correct_answer", "explanation", "source", "difficulty"}
        nulls = [name for name in required & self.model_fields_set if getattr(self, name) is None]
        if nulls:
            raise ValueError(f"Поля не могут быть null: {', '.join(sorted(nulls))}")
        return self


# ---------- Проверка ответа ----------

class AnswerSubmission(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    answer: str = Field(
        min_length=1,
        max_length=100,
        description="Ответ как в бланке ЕГЭ: число, слово или несколько чисел через пробел",
        examples=["4"],
    )


class AnswerResult(BaseModel):
    task_id: int
    is_correct: bool
    correct_answer: str
    explanation: str
