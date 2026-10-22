from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User
from app.modules.notifications import service

router = APIRouter(prefix="/me", tags=["Уведомления"])


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    text: str
    created_at: datetime
    sent_at: datetime | None
    error: str | None


@router.get("/notifications", response_model=list[NotificationRead])
def my_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Последние 20 уведомлений: и отправленные в Telegram, и те, что отправить не удалось."""
    return service.list_for_user(db, user.id)
