"""Что API принимает и отдаёт (контракты).

Pydantic-схемы описывают данные «на границе» сайта и сервера.
Это не таблицы: таблицы описаны в models.py.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.pts.schemas import RankRead

# demo — демоверсия, official — реальный экзамен, approbation — апробация, author — авторская.
Source = Literal["demo", "official", "approbation", "author"]
Sort = Literal["new", "old", "easy", "hard"]
# Фильтр по прогрессу ученика: solved — решённые, unsolved — ещё не решённые верно.
ProgressFilter = Literal["solved", "unsolved"]


# ---------- Номера ЕГЭ ----------

class ExamNumberRead(BaseModel):
    """Карточка на странице «Задания»."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    title: str
    tasks_count: int
    solved: int = Field(default=0, description="Сколько задач номера решил вошедший ученик (0, если не вошёл)")


# ---------- Части задачи (номера 19–21) ----------

class PartRead(BaseModel):
    """Вопрос составной задачи — без ответа."""

    model_config = ConfigDict(from_attributes=True)

    number: str = Field(examples=["19"])
    question: str


class PartAdminRead(PartRead):
    correct_answer: str
    explanation: str


class PartIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    number: str = Field(examples=["19"])
    question: str = Field(min_length=1)
    correct_answer: str = Field(min_length=1, max_length=100, examples=["29"])
    explanation: str = ""


# ---------- Картинки и файлы ----------

class TaskFileRead(BaseModel):
    """Картинка или файл к задаче."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: Literal["image", "attachment"]
    name: str = Field(validation_alias="original_name", description="Имя, с которым файл скачивается")
    url: str = Field(description="Адрес файла в API, например /files/3f2a….xlsx")
    size: int = Field(description="Размер в байтах")
    content_type: str


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
    image_url: str | None = Field(description="Картинка по внешней ссылке (старый способ; новые — в images)")
    images: list[TaskFileRead] = Field(
        default=[],
        validation_alias="student_images",
        description="Загруженные картинки для ученика (без вставленных только в разбор — они видны после ответа)",
    )
    attachments: list[TaskFileRead] = Field(default=[], description="Файлы к заданию для скачивания")
    source: Source
    source_label: str | None
    difficulty: int
    parts: list[PartRead] = Field(
        default=[], description="Вопросы 19, 20, 21 — только у задач номера «19-21»; у остальных пусто"
    )
    status: Literal["solved", "failed"] | None = Field(
        default=None,
        description="Для вошедшего ученика: solved — решена, failed — пробовал, но неверно",
    )
    solution_available: bool = Field(
        default=False,
        description="Можно открыть ответ и разбор (GET /tasks/{id}/solution): задача решена "
                    "или неверных ответов набралось достаточно",
    )


class TaskPage(BaseModel):
    """Страница прототипов одного номера."""

    number: ExamNumberRead
    total: int = Field(description="Сколько задач подходит под фильтр всего")
    items: list[TaskRead]


class TaskAdminRead(TaskRead):
    """Задача для автора: с ответом и разбором."""

    correct_answer: str = Field(description="У задач 19–21 — сводка ответов частей, например «19: 29; 20: 28 48; 21: 44»")
    explanation: str
    created_at: datetime
    images: list[TaskFileRead] = Field(default=[], validation_alias="images", description="Все картинки задачи")
    parts: list[PartAdminRead] = []


class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    exam_number: str = Field(examples=["1"])
    statement: str = Field(min_length=1)
    image_url: str | None = Field(default=None, max_length=500)
    correct_answer: str | None = Field(
        default=None, min_length=1, max_length=100, examples=["42"],
        description="Обязателен для всех номеров, кроме «19-21»: там ответы — в parts",
    )
    explanation: str = ""
    source: Source = "author"
    source_label: str | None = Field(default=None, max_length=100)
    difficulty: int = Field(default=50, ge=0, le=100)
    parts: list[PartIn] | None = Field(
        default=None, description="Только для «19-21»: ровно три вопроса с номерами 19, 20, 21"
    )


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
    parts: list[PartIn] | None = Field(default=None, description="Заменить вопросы 19–21 целиком")

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
    """Обычная задача — поле answer. Задача 19–21 — поле answers: {"19": "29", "20": "28 48", "21": "44"}."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    answer: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="Ответ как в бланке ЕГЭ: число, слово или несколько чисел через пробел",
        examples=["4"],
    )
    answers: dict[str, str] | None = Field(
        default=None, description="Ответы на части 19–21; пустая строка — нет ответа", examples=[{"19": "29"}]
    )

    @model_validator(mode="after")
    def one_of(self):
        if (self.answer is None) == (self.answers is None):
            raise ValueError("Пришлите либо answer, либо answers")
        if self.answers is not None:
            self.answers = {k.strip(): v.strip() for k, v in self.answers.items()}
            if any(len(v) > 100 for v in self.answers.values()):
                raise ValueError("Ответ длиннее 100 символов")
        return self


class PartResult(BaseModel):
    number: str
    answer: str
    is_correct: bool
    correct_answer: str | None = Field(description="null, если часть решена неверно и разбор ещё закрыт")
    explanation: str | None = Field(description="null, если часть решена неверно и разбор ещё закрыт")


class AnswerResult(BaseModel):
    """Результат проверки.

    Верный ответ — с правильным ответом и разбором. Неверный — без них: ученик
    пробует ещё. Когда неверных ответов набралось reveal_after_wrong (3),
    solution_available = true и ответ с разбором можно открыть: GET /tasks/{id}/solution.
    """

    task_id: int
    is_correct: bool = Field(description="У задачи 19–21 — верны все три части")
    correct_answer: str | None = Field(description="null при неверном ответе")
    explanation: str | None = Field(description="null при неверном ответе")
    parts: list[PartResult] = []
    solution_available: bool = Field(description="Можно открыть ответ и разбор")
    attempts_left: int = Field(description="Сколько ещё неверных ответов — и откроется разбор (0 — уже открыт)")
    pts_delta: float | None = Field(
        default=None,
        description="Изменение PTS за этот ответ; null — не засчитан (гость или повторная попытка)",
    )
    pts_repeat: bool = Field(default=False, description="Повторная попытка вошедшего: PTS не меняются")
    rank_up: RankRead | None = Field(default=None, description="Новый ранг выше прежнего — показать поздравление")


class PartSolution(BaseModel):
    number: str
    correct_answer: str
    explanation: str


class TaskSolution(BaseModel):
    """Ответ и разбор задачи (после решения или нескольких неверных попыток)."""

    task_id: int
    correct_answer: str
    explanation: str
    parts: list[PartSolution] = []
