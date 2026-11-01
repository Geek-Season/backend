"""GET /me/rank — карточка ранга в профиле."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User
from app.modules.pts import ranks, service
from app.modules.pts.schemas import MyRank, rank_read
from app.modules.rating.service import immortal_places

router = APIRouter(prefix="/me", tags=["Прогресс"])


@router.get("/rank", response_model=MyRank)
def my_rank(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ранг, PTS и прогноз балла. До входного пробника ранга нет (calibrated = false)."""
    rank = ranks.by_key(user.rank_key)
    calibrated = user.calibrated_at is not None and rank is not None
    forecast = service.user_forecast(db, user) if calibrated else None
    up = ranks.next_up(rank) if rank else None
    return {
        "calibrated": calibrated,
        "rank": rank_read(rank),
        "pts": round(user.pts, 1),
        "games": user.pts_games,
        "forecast_primary": forecast.primary if forecast else None,
        "forecast_test": forecast.test_score if forecast else None,
        "rank_min_primary": rank.min_primary if rank else None,
        "next_rank": rank_read(up),
        "next_min_primary": up.min_primary if up else None,
        "immortal_place": immortal_places(db).get(user.id) if rank and rank.key == "immortal" else None,
    }
