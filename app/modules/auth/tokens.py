"""JWT — «пропуск» пользователя.

После входа сайт получает строку-токен и прикладывает её к каждому запросу
в заголовке `Authorization: Bearer <токен>`. Внутри токена записан id
пользователя и срок действия, а подпись (jwt_secret) не даёт его подделать:
изменишь хоть букву — подпись не сойдётся.
"""

from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import settings

ALGORITHM = "HS256"


def create_access_token(user_id: int) -> str:
    expires = datetime.now(UTC) + timedelta(days=settings.jwt_expire_days)
    payload = {"sub": str(user_id), "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def read_user_id(token: str) -> int | None:
    """id пользователя из токена или None, если токен поддельный или истёк."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
