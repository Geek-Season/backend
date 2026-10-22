from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User
from app.modules.achievements import service

router = APIRouter(prefix="/me", tags=["Достижения"])


class AchievementRead(BaseModel):
    code: str
    icon: str
    title: str
    description: str
    received_at: datetime | None  # None — ещё не получено


@router.get("/achievements", response_model=list[AchievementRead])
def my_achievements(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Все достижения: полученные — с датой, остальные — с received_at = null.

    Выдаёт их фоновый воркер по событиям из RabbitMQ, обычно в течение секунды.
    """
    return service.list_for_user(db, user.id)
