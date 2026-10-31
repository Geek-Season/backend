from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.pts.schemas import RankRead


class AdminUserRow(BaseModel):
    """Строка в списке учеников админки."""

    id: int
    telegram_id: int
    display_name: str
    nickname: str | None
    first_name: str
    last_name: str | None
    username: str | None
    avatar_url: str | None
    role: Literal["student", "admin"]
    blocked: bool
    rank: RankRead | None
    pts: int
    games: int = Field(description="Сколько задач засчитано в PTS")
    solved: int = Field(description="Сколько разных задач решено")
    created_at: datetime
    last_login_at: datetime | None


class AdminUserList(BaseModel):
    total: int
    items: list[AdminUserRow]


class Person(BaseModel):
    id: int
    name: str


class ActionRead(BaseModel):
    id: int
    action: str
    text: str = Field(description="Что сделано, словами: «изменил PTS: 0 → 1200»")
    details: dict
    created_at: datetime
    admin: Person | None
    user: Person | None


class ActionList(BaseModel):
    items: list[ActionRead]
    next_before: int | None = Field(description="Для следующей страницы: ?before=<это число>")


class AdminUserDetail(AdminUserRow):
    gender: Literal["m", "f"] | None
    has_custom_avatar: bool
    pts_rd: int
    calibrated_at: datetime | None
    blocked_at: datetime | None
    forecast_primary: int | None = Field(description="Прогноз первичного балла (только при ранге)")
    forecast_score: int | None = Field(description="Прогноз тестового балла (только при ранге)")
    attempts: int
    exams: int
    achievements: int
    admin_from_env: bool = Field(description="В ADMIN_TELEGRAM_IDS: права вернутся при следующем входе")
    actions: list[ActionRead]


class UserPatch(BaseModel):
    """Меняются только присланные поля.

    nickname: строка — новый ник (те же правила, что у ученика); null — сбросить.
    created_at: дата «на сайте с» (в профиле — «Занимаюсь с …»).
    role: student или admin. blocked: true — заблокировать, false — разблокировать.
    """

    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(default=None, max_length=100)
    created_at: datetime | None = None
    role: Literal["student", "admin"] | None = None
    blocked: bool | None = None


class PtsChange(BaseModel):
    """Ровно одно из двух: pts — выставить число; rank — выбрать ранг (PTS подберётся)."""

    model_config = ConfigDict(extra="forbid")

    pts: int | None = Field(default=None, ge=0, le=3000)
    rank: Literal["immortal", "divine", "overlord", "legend", "hero", "knight", "recruit"] | None = None
