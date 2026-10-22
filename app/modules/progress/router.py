from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User
from app.modules.progress import service
from app.modules.progress.schemas import UserStats

router = APIRouter(prefix="/me", tags=["Прогресс"])


@router.get("/stats", response_model=UserStats)
def my_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Статистика вошедшего ученика для страницы профиля."""
    return service.user_stats(db, user.id)
