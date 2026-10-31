from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str = Field(description="Как ученика зовут на сайте: ник или имя из Telegram")
    nickname: str | None = Field(description="Ник, если ученик его задал")
    first_name: str
    last_name: str | None
    username: str | None
    role: Literal["student", "admin"]
    created_at: datetime


class NicknameUpdate(BaseModel):
    """Сменить ник. null или пустая строка — убрать ник и снова показывать имя из Telegram."""

    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(examples=["kotik_42"], max_length=100)


class LoginStart(BaseModel):
    """Ключ для QR-кода и ссылка на бота."""

    token: str
    bot_link: str = Field(examples=["https://t.me/GeekSeasonBot?start=abc123"])
    expires_in: int = Field(description="Сколько секунд действует ключ")


class TokenResponse(BaseModel):
    """Успешный вход: токен для заголовка Authorization: Bearer <access_token>."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserRead


class LoginStatus(BaseModel):
    """Ответ на вопрос сайта «подтвердили ли вход?».

    pending   — ещё ждём нажатия «Да» в боте;
    declined  — в боте нажали «Нет»;
    expired   — ключ устарел или уже использован;
    confirmed — вход подтверждён, в ответе есть access_token и user.
    """

    status: Literal["pending", "declined", "expired", "confirmed"]
    access_token: str | None = None
    user: UserRead | None = None


class CodeLogin(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(pattern=r"^\d{6}$", description="6 цифр из бота", examples=["482913"])
