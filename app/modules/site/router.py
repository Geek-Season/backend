"""Настройки сайта.

GET /site            — открыто всем: {exam_date} (null — счётчик до ЕГЭ не показывается)
PUT /admin/site      — админ: {exam_date: "2027-06-08" | null}; пишется в журнал
"""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.admin import service as admin_service
from app.modules.auth.deps import require_admin
from app.modules.auth.models import User
from app.modules.site.models import EXAM_DATE, SiteSetting

router = APIRouter(tags=["Сайт"])


class SiteRead(BaseModel):
    exam_date: date | None = Field(description="Дата ЕГЭ по информатике для счётчика на главной")


class SiteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam_date: date | None


def _read(db: Session) -> dict:
    row = db.get(SiteSetting, EXAM_DATE)
    return {"exam_date": date.fromisoformat(row.value) if row and row.value else None}


@router.get("/site", response_model=SiteRead)
def get_site(db: Session = Depends(get_db)):
    return _read(db)


@router.put("/admin/site", response_model=SiteRead)
def update_site(body: SiteUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    before = _read(db)["exam_date"]
    row = db.get(SiteSetting, EXAM_DATE) or SiteSetting(key=EXAM_DATE)
    row.value = body.exam_date.isoformat() if body.exam_date else None
    db.add(row)
    if before != body.exam_date:
        admin_service.log(db, admin, None, "site.exam_date", {
            "before": {"exam_date": before.isoformat() if before else None},
            "after": {"exam_date": row.value},
        })
    db.commit()
    return _read(db)
