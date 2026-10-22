"""Подключение к PostgreSQL.

engine  — «телефонная линия» до базы, создаётся один раз.
Session — один «разговор» с базой; на каждый HTTP-запрос открываем свой.
Base    — родительский класс для всех таблиц (моделей).
"""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """Выдаёт сессию обработчику запроса и закрывает её после ответа.

    Используется так: `def handler(db: Session = Depends(get_db))`.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
