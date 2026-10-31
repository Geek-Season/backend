"""Что API отдаёт про PTS и ранги."""

from pydantic import BaseModel, Field


class RankRead(BaseModel):
    key: str = Field(examples=["hero"])
    title: str = Field(examples=["Герой рекурсии"], description="Полное название — для профиля")
    short: str = Field(examples=["Герой"], description="Короткое — для рейтинга и шапки")


class MyRank(BaseModel):
    """Карточка ранга в профиле. Видит только сам ученик: PTS и прогноз — не для других."""

    calibrated: bool = Field(description="Был ли входной пробник; без него ранга нет")
    rank: RankRead | None
    pts: float
    games: int = Field(description="Сколько задач засчитано в PTS")
    forecast_primary: int | None = Field(description="Прогноз первичного балла (после входного пробника)")
    forecast_test: int | None = Field(description="Прогноз тестового балла")
    rank_min_primary: int | None = Field(description="С какого первичного балла начинается текущий ранг")
    next_rank: RankRead | None = Field(description="Следующий ранг (null — уже Immortal)")
    next_min_primary: int | None


def rank_read(rank) -> dict | None:
    return {"key": rank.key, "title": rank.title, "short": rank.short} if rank else None
