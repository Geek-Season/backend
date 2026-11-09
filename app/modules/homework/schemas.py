"""Что API домашек принимает и отдаёт."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.pts.schemas import RankRead
from app.modules.tasks.schemas import Source, TaskRead, TaskSolution


# ---------- Общее ----------

class StudentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    avatar_url: str | None


class PartAnswer(BaseModel):
    number: str = Field(examples=["19"])
    answer: str
    is_correct: bool


# ---------- Ученик ----------

class HomeworkCard(BaseModel):
    """Карточка в списке «Домашки»."""

    id: int
    title: str
    comment: str | None
    numbers: list[str] = Field(description="Коды номеров ЕГЭ в домашке по порядку, например [\"16\", \"23\"]")
    total: int = Field(description="Сколько задач")
    answered: int = Field(description="На сколько уже ответил")
    correct: int = Field(description="Сколько верно")
    sent_at: datetime
    submitted_at: datetime | None = Field(description="null — ещё не сдана")


class HomeworkItem(BaseModel):
    """Задача домашки с ответом ученика. Разбор — только после сдачи."""

    position: int
    task: TaskRead
    answer: str | None = Field(description="Ответ ученика; null — ещё не отвечал")
    is_correct: bool | None = Field(description="null — не отвечал (после сдачи такая задача считается неверной)")
    parts: list[PartAnswer] | None = Field(default=None, description="Только у 19–21: ответы частей")
    solution: TaskSolution | None = Field(default=None, description="Правильный ответ и разбор — после сдачи")


class HomeworkDetail(BaseModel):
    id: int
    title: str
    comment: str | None
    sent_at: datetime
    submitted_at: datetime | None
    total: int
    answered: int
    correct: int
    items: list[HomeworkItem]


class HomeworkAnswerResult(BaseModel):
    """Результат единственной попытки. Правильный ответ и разбор не отдаются до сдачи."""

    task_id: int
    is_correct: bool
    parts: list[PartAnswer] = []
    pts_delta: float | None = Field(default=None, description="Изменение PTS; null — не засчитан (повторная встреча с задачей)")
    pts_repeat: bool = False
    rank_up: RankRead | None = None


# ---------- Админ ----------

class HomeworkIn(BaseModel):
    """Создать черновик."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=2000)
    task_ids: list[int] = Field(default=[], max_length=100, description="Задачи по порядку")
    user_ids: list[int] = Field(default=[], max_length=500)

    @field_validator("comment")
    @classmethod
    def empty_comment_is_null(cls, value: str | None) -> str | None:
        return value or None


class HomeworkPatch(BaseModel):
    """Меняются только присланные поля. После отправки — только title и comment."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=120)
    comment: str | None = Field(default=None, max_length=2000)
    task_ids: list[int] | None = Field(default=None, max_length=100)
    user_ids: list[int] | None = Field(default=None, max_length=500)

    @field_validator("comment")
    @classmethod
    def empty_comment_is_null(cls, value: str | None) -> str | None:
        return value or None


class StudentsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[int] = Field(min_length=1, max_length=500)


class HomeworkRow(BaseModel):
    """Строка в списке домашек админки."""

    id: int
    title: str
    created_at: datetime
    sent_at: datetime | None = Field(description="null — черновик")
    numbers: list[str]
    tasks_count: int
    students_count: int
    submitted_count: int


class PickerTask(BaseModel):
    """Задача в конструкторе домашки."""

    id: int
    exam_number: str
    statement: str
    source: Source
    source_label: str | None
    difficulty: int
    is_archived: bool
    tried_by: list[int] = Field(default=[], description="Кто из выбранных учеников уже решал задачу (верно или нет)")


class PickerPage(BaseModel):
    total: int
    items: list[PickerTask]


class RandomIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: str = Field(examples=["16"])
    count: int = Field(default=3, ge=1, le=20)
    user_ids: list[int] = Field(default=[], max_length=500, description="Не брать задачи, которые они уже решали")
    exclude_task_ids: list[int] = Field(default=[], max_length=500, description="Уже выбранные задачи")


class HomeworkAdminDetail(BaseModel):
    """Домашка для конструктора."""

    id: int
    title: str
    comment: str | None
    created_at: datetime
    sent_at: datetime | None
    tasks: list[PickerTask]
    students: list[StudentRead]


class ResultCell(BaseModel):
    answer: str
    is_correct: bool
    parts: list[PartAnswer] | None = None
    answered_at: datetime


class ResultRow(BaseModel):
    student: StudentRead
    submitted_at: datetime | None
    answered: int
    correct: int
    cells: list[ResultCell | None] = Field(description="По задачам в порядке домашки; null — не отвечал")


class ResultTask(BaseModel):
    position: int
    task_id: int
    exam_number: str


class HomeworkResults(BaseModel):
    """Таблица «ученик × задача»."""

    id: int
    title: str
    sent_at: datetime | None
    tasks: list[ResultTask]
    rows: list[ResultRow]
