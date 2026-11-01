"""Настройки сайта, которые меняет админ (ключ → значение). Сейчас одна: дата ЕГЭ для счётчика на главной."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

EXAM_DATE = "exam_date"  # 2027-06-08 — дата ЕГЭ по информатике для «До ЕГЭ — N дней»


class SiteSetting(Base):
    __tablename__ = "site_settings"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
