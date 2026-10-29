"""Картинки и файлы к задачам: проверка, сохранение на диск, удаление.

Картинка (image) показывается прямо в условии задачи. Разрешены PNG, JPEG,
GIF и WebP; тип проверяется по содержимому файла («магическим» байтам), а не
только по расширению — переименованный .exe картинкой не станет. SVG не
принимаем: внутри него может быть JavaScript.

Файл к заданию (attachment) ученик скачивает — как на ЕГЭ, где к номерам
3, 9, 10, 17, 18, 22, 24, 26, 27 прилагаются таблицы и текстовые файлы.
Разрешены только расширения из ATTACHMENT_TYPES.

На диске файл лежит под случайным именем (32 hex-символа + расширение),
поэтому имя от пользователя никогда не попадает в путь на сервере.
"""

import re
import secrets
from pathlib import Path
from urllib.parse import quote

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.tasks.models import FILE_ATTACHMENT, FILE_IMAGE, Task, TaskFile

# расширение → MIME-тип, с которым файл отдаётся ученику
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

ATTACHMENT_TYPES = {
    ".txt": "text/plain; charset=utf-8",
    ".csv": "text/csv; charset=utf-8",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
}

# Имя файла на диске: только то, что мы сами сгенерировали.
STORED_NAME = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{1,5}$")

_CHUNK = 1024 * 1024


class FileRejected(Exception):
    """Файл не подходит: тип, размер или лимит на число файлов. Текст — для пользователя."""


def upload_dir() -> Path:
    path = Path(settings.upload_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _image_extension(head: bytes) -> str | None:
    """Настоящий тип картинки по первым байтам файла."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def _clean_name(name: str | None, fallback: str) -> str:
    """Имя для скачивания: без путей и управляющих символов, не длиннее 200."""
    name = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable() and ch not in '"<>:|?*').strip(" .")
    return name[:200] or fallback


_TRANSLIT = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
     "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"],
))


def _ascii_fallback(name: str) -> str:
    """«задание 9.xlsx» → «zadanie 9.xlsx»: имя для браузеров, не понимающих filename*."""
    out = []
    for ch in name:
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.capitalize() if ch != low else t)
        elif ch.isascii() and ch.isprintable() and ch not in '"\\':
            out.append(ch)
        else:
            out.append("_")
    return "".join(out) or "file"


def content_disposition(disposition: str, name: str) -> str:
    """Заголовок Content-Disposition с именем файла по RFC 6266.

    Два имени: filename — латиницей (запасное), filename* — настоящее, в UTF-8.
    Только с filename* часть браузеров (например, Chromium при скачивании с другого
    адреса — сайт на GitHub Pages, файл с API) сохраняет файл как «download».
    """
    return f"{disposition}; filename=\"{_ascii_fallback(name)}\"; filename*=UTF-8''{quote(name, safe='')}"


def _read_limited(upload: UploadFile, limit: int) -> bytes:
    """Прочитать файл целиком, но не больше limit байт — иначе FileRejected."""
    data = bytearray()
    while chunk := upload.file.read(_CHUNK):
        data += chunk
        if len(data) > limit:
            raise FileRejected(f"Файл больше {limit // (1024 * 1024)} МБ")
    return bytes(data)


def save_file(db: Session, task: Task, upload: UploadFile, kind: str) -> TaskFile:
    """Проверить файл, сохранить на диск и привязать к задаче."""
    count = db.scalar(
        select(func.count(TaskFile.id)).where(TaskFile.task_id == task.id, TaskFile.kind == kind)
    )
    if count >= settings.max_files_per_kind:
        what = "картинок" if kind == FILE_IMAGE else "файлов"
        raise FileRejected(f"К задаче можно прикрепить не больше {settings.max_files_per_kind} {what}")

    original_ext = Path(upload.filename or "").suffix.lower()
    if kind == FILE_IMAGE:
        data = _read_limited(upload, settings.max_image_mb * 1024 * 1024)
        ext = _image_extension(data[:16])
        if ext is None:
            raise FileRejected("Это не картинка: подходят PNG, JPEG, GIF и WebP")
        content_type = IMAGE_TYPES[ext]
    else:
        if original_ext not in ATTACHMENT_TYPES:
            allowed = ", ".join(sorted(ATTACHMENT_TYPES))
            raise FileRejected(f"Такой тип файла нельзя прикрепить. Подходят: {allowed}")
        data = _read_limited(upload, settings.max_attachment_mb * 1024 * 1024)
        ext = original_ext
        content_type = ATTACHMENT_TYPES[ext]
    if not data:
        raise FileRejected("Файл пустой")

    stored_name = f"{secrets.token_hex(16)}{ext}"
    path = upload_dir() / stored_name
    path.write_bytes(data)

    position = db.scalar(select(func.coalesce(func.max(TaskFile.position), 0)).where(TaskFile.task_id == task.id))
    record = TaskFile(
        task_id=task.id,
        kind=kind,
        stored_name=stored_name,
        original_name=_clean_name(upload.filename, f"file{ext}"),
        content_type=content_type,
        size=len(data),
        position=position + 1,
    )
    db.add(record)
    try:
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)  # в базу не записалось — файл на диске не нужен
        raise
    db.refresh(task)
    return record


def get_task_file(db: Session, task_id: int, file_id: int) -> TaskFile | None:
    record = db.get(TaskFile, file_id)
    return record if record is not None and record.task_id == task_id else None


def delete_file(db: Session, record: TaskFile) -> None:
    """Удалить файл из базы и с диска."""
    stored_name = record.stored_name
    db.delete(record)
    db.commit()
    (upload_dir() / stored_name).unlink(missing_ok=True)


def find_stored(db: Session, stored_name: str) -> tuple[TaskFile, Path] | None:
    """Файл по имени на диске — для отдачи ученику. None, если нет такого."""
    if not STORED_NAME.match(stored_name):
        return None
    record = db.scalar(select(TaskFile).where(TaskFile.stored_name == stored_name))
    if record is None:
        return None
    path = upload_dir() / stored_name
    return (record, path) if path.is_file() else None


KINDS = (FILE_IMAGE, FILE_ATTACHMENT)
