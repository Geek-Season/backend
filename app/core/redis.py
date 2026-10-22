"""Подключение к Redis.

Redis — это быстрый словарь «ключ → значение» в памяти, где у ключа может быть
срок жизни (TTL). Он удобен для всего временного: коды входа живут 5 минут
и исчезают сами, без всякой уборки с нашей стороны.
"""

from redis import Redis

from app.core.config import settings

redis_client: Redis = Redis.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> Redis:
    """Зависимость FastAPI: `def handler(r: Redis = Depends(get_redis))`.

    В тестах её подменяют на ненастоящий Redis (fakeredis).
    """
    return redis_client
