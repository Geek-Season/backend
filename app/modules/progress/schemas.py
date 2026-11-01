from datetime import date, datetime

from pydantic import BaseModel, Field


class DayActivity(BaseModel):
    date: date
    attempts: int
    correct: int


class Calendar(BaseModel):
    """Календарь активности за последний год: в days — только дни, когда были ответы."""

    start: date
    end: date = Field(description="Сегодня (по Москве)")
    days: list[DayActivity]
    total: int = Field(description="Ответов за год")
    active_days: int = Field(description="Дней с ответами за год")
    best_streak: int = Field(description="Лучшая серия дней подряд за год")
    streak: int = Field(description="Текущая серия дней подряд")


class NumberProgress(BaseModel):
    code: str
    title: str
    tasks_count: int
    attempted: int = Field(description="Сколько разных задач номера ученик пробовал")
    solved: int = Field(description="Сколько задач номера решено верно")


class WeakNumber(BaseModel):
    code: str
    title: str
    attempts: int
    correct: int
    accuracy: float
    unsolved_tasks: int = Field(description="Задач номера, которые пробовал, но не решил")


class RecentAttempt(BaseModel):
    task_id: int
    exam_number: str
    answer: str
    is_correct: bool
    created_at: datetime


class UserStats(BaseModel):
    solved_tasks: int
    attempted_tasks: int
    total_tasks: int = Field(description="Сколько задач всего в банке")
    total_attempts: int
    correct_attempts: int
    accuracy: float | None = Field(description="Доля верных ответов от 0 до 1; null — ответов ещё нет")
    streak_days: int = Field(description="Дней подряд с хотя бы одним ответом")
    active_days: int
    activity: list[DayActivity] = Field(description="Последние 14 дней, по порядку (сайт рисует calendar)")
    calendar: Calendar
    by_number: list[NumberProgress]
    weak_numbers: list[WeakNumber] = Field(description="Что повторить: до 5 номеров, сначала самые проблемные")
    recent: list[RecentAttempt]
