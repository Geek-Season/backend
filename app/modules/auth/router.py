"""HTTP-адреса входа.

POST /auth/telegram/start           — получить ключ и ссылку для QR
GET  /auth/telegram/status/{ключ}   — сайт спрашивает, подтвердили ли вход
POST /auth/code                     — войти по коду из бота
GET  /auth/me                       — кто я (нужен токен)
PATCH /auth/me                      — сменить ник {"nickname": "..."} (null — убрать)
DELETE /auth/users/{id}/nickname    — сбросить чужой ник (только админ)
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis import Redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.redis import get_redis
from app.modules.auth import nickname, service
from app.modules.auth.deps import get_current_user, require_admin
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    CodeLogin,
    LoginStart,
    LoginStatus,
    NicknameUpdate,
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


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _too_many() -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Слишком много попыток. Подождите минуту",
        headers={"Retry-After": "60"},
    )


@router.post("/telegram/start", response_model=LoginStart)
def telegram_start(request: Request, r: Redis = Depends(get_redis)):
    """Новый QR-ключ. Не больше QR_STARTS_PER_MINUTE (10) в минуту с одного адреса — иначе 429."""
    if not settings.bot_username:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Вход через Telegram не настроен: укажите BOT_USERNAME в .env",
        )
    try:
        token = service.start_qr_login(r, _client(request))
    except service.TooManyAttempts:
        raise _too_many() from None
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
    try:
        user_id = service.redeem_code(r, data.code, _client(request))
    except service.TooManyAttempts:
        raise _too_many() from None
    if user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный или устаревший код")
    return _token_response(db, user_id)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserRead)
def update_me(body: NicknameUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Сменить ник: 3–20 символов, буквы, цифры, «_» и «-», без мата. null — убрать ник."""
    try:
        user.nickname = nickname.clean(body.nickname)
    except nickname.NicknameError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    db.commit()
    return user


@router.delete("/users/{user_id}/nickname", status_code=status.HTTP_204_NO_CONTENT)
def reset_nickname(user_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Сбросить неприличный ник: ученик снова будет показан под именем из Telegram."""
    target = service.get_user(db, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    target.nickname = None
    db.commit()
