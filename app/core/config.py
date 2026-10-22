"""Настройки приложения.

Значения берутся из переменных окружения или файла .env.
Так один и тот же код работает и на твоём компьютере, и в Docker:
меняется только адрес базы, а не код.

Секреты (токен бота, ключ JWT) живут ТОЛЬКО в .env — он не попадает в Git.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Адрес базы: postgresql+psycopg://пользователь:пароль@хост:порт/имя_базы
    database_url: str = "postgresql+psycopg://geek:geek@localhost:5433/geek_season"
    # Redis: быстрые временные данные (коды входа, счётчики попыток).
    redis_url: str = "redis://localhost:6380/0"

    # Telegram-бот: токен выдаёт @BotFather, имя — без @, например GeekSeasonBot.
    bot_token: str = ""
    bot_username: str = ""

    # Ключ для подписи токенов входа (JWT). В .env задай свою длинную случайную строку.
    jwt_secret: str = "dev-secret-change-me-in-env-file-0123456789"
    jwt_expire_days: int = 7

    # Часовой пояс, по которому статистика считает «дни» (серия, график активности).
    timezone: str = "Europe/Moscow"

    # Сколько секунд живут QR-ключ и код из бота.
    login_ttl_seconds: int = 300
    # Сколько попыток ввести код можно сделать за минуту с одного адреса.
    code_attempts_per_minute: int = 5


settings = Settings()
