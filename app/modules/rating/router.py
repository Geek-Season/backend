from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from redis import Redis
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.redis import get_redis
from app.modules.auth.deps import get_optional_user
from app.modules.auth.models import User
from app.modules.pts.schemas import RankRead
from app.modules.rating import service

router = APIRouter(prefix="/rating", tags=["Рейтинг"])


class RatingRow(BaseModel):
    """Строка рейтинга. Рейтинг открыт всем, поэтому здесь нет ничего, по чему
    ученика можно найти в Telegram: ни @username, ни Telegram ID, ни фамилии."""

    place: int
    user_id: int  # внутренний номер на сайте, не Telegram ID
    name: str  # ник, а если его нет — имя из Telegram
    has_nickname: bool
    avatar_url: str | None
    has_custom_avatar: bool
    rank: RankRead | None = None
    points: int
    is_me: bool


class MyPlace(BaseModel):
    place: int | None  # None — ещё нет очков
    points: int


class Rating(BaseModel):
    period: Literal["week", "all"]
    participants: int
    top: list[RatingRow]
    me: MyPlace | None


@router.get("", response_model=Rating)
def get_rating(
    period: Literal["week", "all"] = "week",
    limit: int = Query(20, ge=1, le=100),
    r: Redis = Depends(get_redis),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Таблица лидеров. Очко — за каждую задачу, решённую впервые.

    Вошедшему ученику в поле me — его место, даже если он не в топе.
    """
    return service.leaderboard(r, db, period, limit, user.id if user else None)
