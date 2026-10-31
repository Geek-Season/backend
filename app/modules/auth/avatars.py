"""Аватарки учеников.

По умолчанию — фото профиля из Telegram: после каждого входа сервер в фоне
спрашивает у Telegram текущее фото (getUserProfilePhotos) и, если оно сменилось,
скачивает свою копию — адрес файла в Telegram содержит токен бота, отдавать его нельзя.
Если ученик скрыл фото настройками приватности или удалил его — аватарки нет (буква).

Ученик может загрузить свою (сайт сам обрезает её до квадрата) — тогда фото из Telegram
её не трогает, пока ученик не нажмёт «Вернуть фото из Telegram» или админ не сбросит
неприличную картинку.

Файлы лежат в uploads/avatars под случайными именами: при каждой смене имя новое,
поэтому браузер может хранить картинку в кэше сколько угодно.
"""

import logging
import re
import secrets
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core import telegram
from app.core.config import settings
from app.core.db import SessionLocal
from app.modules.auth.models import User
from app.modules.tasks.files import _image_extension, upload_dir

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024  # сайт присылает квадрат 512×512 — это десятки КБ
AVATAR_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|webp)$")
CONTENT_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
TELEGRAM_SIZE = 320  # из нескольких размеров фото в Telegram берём ближайший к 320 px


class AvatarRejected(Exception):
    """Файл не подходит. Текст — для ученика."""


def avatar_dir() -> Path:
    path = upload_dir() / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find(name: str) -> tuple[Path, str] | None:
    """Файл аватарки по имени — (путь, тип) или None."""
    if not AVATAR_NAME.match(name):
        return None
    path = avatar_dir() / name
    return (path, CONTENT_TYPES[path.suffix]) if path.is_file() else None


def _store(content: bytes) -> str:
    ext = _image_extension(content[:16])
    if ext not in CONTENT_TYPES:
        raise AvatarRejected("Аватарка — картинка PNG, JPEG или WebP")
    name = secrets.token_hex(16) + ext
    (avatar_dir() / name).write_bytes(content)
    return name


def _drop(name: str | None) -> None:
    if name and AVATAR_NAME.match(name):
        (avatar_dir() / name).unlink(missing_ok=True)


def _replace(user: User, new_name: str | None) -> None:
    old, user.avatar_file = user.avatar_file, new_name
    if old != new_name:
        _drop(old)


# ---------- Своя аватарка ----------

def set_custom(db: Session, user: User, upload: UploadFile) -> None:
    content = upload.file.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise AvatarRejected("Аватарка — не больше 2 МБ")
    if not content:
        raise AvatarRejected("Пустой файл")
    _replace(user, _store(content))
    user.avatar_custom = True
    db.commit()


def forget_custom(db: Session, user: User) -> None:
    """Убрать свою аватарку (ученик вернул фото из Telegram или админ сбросил).

    Фото из Telegram подтянется в фоне — refresh_from_telegram.
    """
    _replace(user, None)
    user.avatar_custom = False
    user.telegram_photo_uid = None
    db.commit()


# ---------- Фото из Telegram ----------

def refresh_from_telegram(user_id: int) -> None:
    """Подтянуть фото профиля из Telegram (в фоне, после входа). Ошибки — только в лог."""
    if not settings.bot_token:
        return
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None or user.avatar_custom:
            return
        try:
            result = telegram.call("getUserProfilePhotos", {"user_id": user.telegram_id, "limit": 1})
            photos = (result or {}).get("photos") or []
            if not photos:  # фото нет или скрыто настройками приватности
                _replace(user, None)
                user.telegram_photo_uid = None
                db.commit()
                return
            size = min(photos[0], key=lambda s: abs(s.get("width", 0) - TELEGRAM_SIZE))
            if size["file_unique_id"] == user.telegram_photo_uid and user.avatar_file:
                return  # то же фото уже скачано
            file = telegram.call("getFile", {"file_id": size["file_id"]})
            content = telegram.download(file["file_path"], MAX_BYTES)
            name = _store(content)
        except (telegram.TelegramError, AvatarRejected, KeyError, TypeError, OSError) as error:
            logger.warning("Фото из Telegram для пользователя %s не получено: %s", user_id, error)
            return
        db.refresh(user)
        if user.avatar_custom:  # пока качали, ученик загрузил свою
            _drop(name)
            return
        _replace(user, name)
        user.telegram_photo_uid = size["file_unique_id"]
        db.commit()
