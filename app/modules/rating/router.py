from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.deps import get_optional_user
from app.modules.auth.models import User
from app.modules.pts.schemas import RankRead
from app.modules.rating import service

router = APIRouter(prefix="/rating", tags=["Рейтинг"])

RankKey = Literal["immortal", "divine", "overlord", "legend", "hero", "knight", "recruit"]


class RatingRow(BaseModel):
    """Строка рейтинга. Рейтинг открыт всем, поэтому здесь нет ничего, по чему
    ученика можно найти в Telegram (ни @username, ни Telegram ID, ни фамилии), и нет чужого PTS."""

    place: int
    user_id: int  # внутренний номер на сайте, не Telegram ID
    name: str  # ник, а если его нет — имя из Telegram
    has_nickname: bool
    avatar_url: str | None
    has_custom_avatar: bool
    rank: RankRead
    immortal_place: int | None = Field(description="Место среди Immortal («Immortal #3»); у остальных — null")
    growth: int | None = Field(description="Прирост PTS за неделю (только в period=week)")
    is_me: bool


class NextRank(BaseModel):
    rank: RankRead
    primary_needed: int = Field(description="Сколько первичных баллов прогноза не хватает")


class MyPlace(BaseModel):
    ranked: bool = Field(description="Есть ранг; без него (до входного пробника) в таблицу не попасть")
    place: int | None = Field(description="Место в этой таблице (null — не в ней)")
    pts: int | None = Field(description="Свой PTS: общий или по номеру (только себе)")
    growth: int | None = Field(description="Свой прирост за неделю")
    row: RatingRow | None = Field(description="Своя строка, если она ниже топа — сайт показывает её после «⋯»")
    next_rank: NextRank | None


class Rating(BaseModel):
    period: Literal["week", "all"]
    number: str | None
    rank: RankKey | None
    participants: int = Field(description="Сколько учеников в таблице (без фильтра по рангу)")
    counts: dict[str, int] = Field(description="{ранг: сколько учеников} — для кнопок фильтра")
    top: list[RatingRow]
    me: MyPlace | None


@router.get("", response_model=Rating)
def get_rating(
    period: Literal["week", "all"] = "all",
    number: str | None = Query(None, max_length=10, description="Код номера ЕГЭ («27», «19-21»): таблица по номеру"),
    rank: RankKey | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Таблица лидеров по PTS: за всё время, за неделю (прирост) или по номеру ЕГЭ.

    Участвуют только ученики с рангом. Другим виден только ранг; свой PTS — в поле me.
    """
    if number is not None and period == "week":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Таблица по номеру — только за всё время")
    try:
        return service.leaderboard(db, period, number, rank, limit, user)
    except service.UnknownNumber:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Номер не найден") from None
