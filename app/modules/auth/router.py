"""HTTP-адреса входа.

POST /auth/telegram/start           — получить ключ и ссылку для QR
GET  /auth/telegram/status/{ключ}   — сайт спрашивает, подтвердили ли вход
POST /auth/code                     — войти по коду из бота
GET  /auth/me                       — кто я (нужен токен)
PATCH /auth/me                      — настройки: {"nickname": "...", "gender": "m" | "f" | null}
PUT  /auth/me/avatar                — своя аватарка (картинка, сайт обрезает до квадрата)
DELETE /auth/me/avatar              — вернуть фото из Telegram
DELETE /auth/users/{id}/nickname    — сбросить чужой ник (только админ)
DELETE /auth/users/{id}/avatar      — сбросить чужую аватарку (только админ)
GET  /avatars/{имя}                 — картинка аватарки
"""

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from redis import Redis
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.redis import get_redis
from app.modules.auth import avatars, nickname, service
from app.modules.auth.deps import BLOCKED_MESSAGE, get_current_user, require_admin
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    CodeLogin,
    LoginStart,
    LoginStatus,
    ProfileUpdate,
    TokenResponse,
    UserRead,
)
from app.modules.auth.tokens import create_access_token

router = APIRouter(prefix="/auth", tags=["Вход"])
avatars_router = APIRouter(prefix="/avatars", tags=["Вход"])


def _token_response(db: Session, user_id: int, background: BackgroundTasks) -> dict:
    user = service.get_user(db, user_id)
    if user is None:  # пользователя удалили, пока шёл вход
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден")
    if user.is_blocked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, BLOCKED_MESSAGE)
    # Фото из Telegram — после ответа: вход не ждёт запросов к Telegram.
    background.add_task(avatars.refresh_from_telegram, user.id)
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
def telegram_status(
    token: str, background: BackgroundTasks, r: Redis = Depends(get_redis), db: Session = Depends(get_db)
):
    """Сайт вызывает каждые 2 секунды, пока статус pending."""
    state, user_id = service.check_qr_login(r, token)
    if state == "confirmed":
        return {"status": "confirmed", **_token_response(db, user_id, background)}
    return {"status": state}


@router.post("/code", response_model=TokenResponse)
def code_login(
    data: CodeLogin,
    request: Request,
    background: BackgroundTasks,
    r: Redis = Depends(get_redis),
    db: Session = Depends(get_db),
):
    try:
        user_id = service.redeem_code(r, data.code, _client(request))
    except service.TooManyAttempts:
        raise _too_many() from None
    if user_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Неверный или устаревший код")
    return _token_response(db, user_id, background)


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)):
    return user


@router.patch("/me", response_model=UserRead)
def update_me(body: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Настройки профиля; меняются только присланные поля.

    nickname — имя на сайте: 3–20 символов, буквы, цифры, пробел, «_» и «-», без мата;
    null — снова имя из Telegram. gender — m, f или null.
    """
    if "nickname" in body.model_fields_set:
        try:
            user.nickname = nickname.clean(body.nickname)
        except nickname.NicknameError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    if "gender" in body.model_fields_set:
        user.gender = body.gender
    db.commit()
    return user


@router.put("/me/avatar", response_model=UserRead)
def upload_avatar(
    file: UploadFile = File(description="PNG, JPEG или WebP до 2 МБ; сайт присылает уже обрезанный квадрат"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Своя аватарка вместо фото из Telegram."""
    try:
        avatars.set_custom(db, user, file)
    except avatars.AvatarRejected as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from None
    return user


@router.delete("/me/avatar", response_model=UserRead)
def reset_my_avatar(background: BackgroundTasks, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Вернуть фото из Telegram (оно подтянется через пару секунд)."""
    avatars.forget_custom(db, user)
    background.add_task(avatars.refresh_from_telegram, user.id)
    return user


@router.delete("/users/{user_id}/nickname", status_code=status.HTTP_204_NO_CONTENT)
def reset_nickname(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Сбросить неприличный ник: ученик снова будет показан под именем из Telegram."""
    from app.modules.admin import service as admin_service  # здесь: админка импортирует модуль входа

    target = service.get_user(db, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    admin_service.set_nickname(db, admin, target, None)
    db.commit()


@router.delete("/users/{user_id}/avatar", status_code=status.HTTP_204_NO_CONTENT)
def reset_avatar(
    user_id: int, background: BackgroundTasks, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    """Сбросить неприличную аватарку: у ученика снова фото из Telegram."""
    from app.modules.admin import service as admin_service

    target = service.get_user(db, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    admin_service.reset_avatar(db, admin, target)
    background.add_task(avatars.refresh_from_telegram, target.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@avatars_router.get("/{name}", response_class=FileResponse)
def get_avatar(name: str):
    """Картинка аватарки. Имя меняется при каждой смене, поэтому кэш — на год."""
    found = avatars.find(name)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аватарка не найдена")
    path, content_type = found
    return FileResponse(path, media_type=content_type, headers={
        "Cache-Control": "public, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
    })
