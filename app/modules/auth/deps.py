"""Проверка «кто делает запрос».

Чтобы адрес API стал доступен только вошедшим, добавь параметр:

    def handler(user: User = Depends(get_current_user)): ...

Без правильного заголовка `Authorization: Bearer <токен>` FastAPI сам ответит 401.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.models import User
from app.modules.auth.service import get_user
from app.modules.auth.tokens import read_user_id

# auto_error=False — чтобы самим вернуть понятное сообщение на русском.
bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        "Нужно войти",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    user_id = read_user_id(credentials.credentials)
    if user_id is None:
        raise unauthorized
    user = get_user(db, user_id)
    if user is None:
        raise unauthorized
    return user
