"""HTTP-адреса входа.

POST /auth/telegram/start           — получить ключ и ссылку для QR
GET  /auth/telegram/status/{ключ}   — сайт спрашивает, подтвердили ли вход
POST /auth/code                     — войти по коду из бота
GET  /auth/me                       — кто я (нужен токен)
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis import Redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.redis import get_redis
from app.modules.auth import service
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    CodeLogin,
    LoginStart,
    LoginStatus,
    TokenResponse,
    UserRead,
)
from app.modules.auth.tokens import create_access_token

router = APIRouter(prefix="/auth", tags=["Вход"])


def _token_response(db: Session, user_id: int) -> dict:
    user = service.get_user(db, user_id)
    if user is None:  # пользователя удалили, пока шёл вход
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    return {"access_token": create_access_token(user.id), "user": user}


@router.post("/telegram/start", response_model=LoginStart)
def telegram_start(r: Redis = Depends(get_redis)):
    if not settings.bot_username:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Вход через Telegram не настроен: укажите BOT_USERNAME в .env",
        )
    token = service.start_qr_login(r)
    return {
        "token": token,
        "bot_link": f"https://t.me/{settings.bot_username}?start={token}",
        "expires_in": settings.login_ttl_seconds,
    }


@router.get("/telegram/status/{token}", response_model=LoginStatus)
def telegram_status(token: str, r: Redis = Depends(get_redis), db: Session = Depends(get_db)):
    """Сайт вызывает каждые 2 секунды, пока статус pending."""
    state, user_id = service.check_qr_login(r, token)
    if state == "confirmed":
        return {"status": "confirmed", **_token_response(db, user_id)}
    return {"status": state}


@router.post("/code", response_model=TokenResponse)
def code_login(
    data: CodeLogin,
    request: Request,
    r: Redis = Depends(get_redis),
    db: Session = Depends(get_db),
):
    client = request.client.host if request.client else "unknown"
    try:
        user_id = service.redeem_code(r, data.code, client)
    except service.TooManyAttempts:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток. Подождите минуту"
        ) from None
    if user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный или устаревший код")
    return _token_response(db, user_id)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)):
    return user
