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

    # Брокер сообщений RabbitMQ. Пустая строка — брокер выключен (события не отправляются).
    rabbitmq_url: str = "amqp://geek:geek@localhost:5672/"

    # Telegram-бот: токен выдаёт @BotFather, имя — без @, например GeekSeasonBot.
    bot_token: str = ""
    bot_username: str = ""
    # Адрес Telegram Bot API. Менять не нужно; другой адрес — для тестов или своего Bot API сервера.
    telegram_api_url: str = "https://api.telegram.org"

    # Telegram ID администраторов через запятую: ADMIN_TELEGRAM_IDS=123456789,987654321
    # При входе такой пользователь автоматически получает роль admin.
    # Свой ID можно узнать у бота @userinfobot.
    admin_telegram_ids: str = ""

    @property
    def admin_ids(self) -> set[int]:
        return {int(part) for part in self.admin_telegram_ids.replace(" ", "").split(",") if part}

    # Ключ для подписи токенов входа (JWT). В .env задай свою длинную случайную строку.
    jwt_secret: str = "dev-secret-change-me-in-env-file-0123456789"
    jwt_expire_days: int = 7

    # Длительность пробника в минутах. На ЕГЭ по информатике — 235 (3 ч 55 мин).
    # Переопределяется в .env: EXAM_DURATION_MINUTES
    exam_duration_minutes: int = 235

    # Часовой пояс, по которому статистика считает «дни» (серия, график активности).
    timezone: str = "Europe/Moscow"

    # Папка для загруженных картинок и файлов к задачам. В Docker — отдельный том,
    # чтобы файлы переживали пересборку контейнера.
    upload_dir: str = "uploads"
    # Ограничения на размер загрузки, в мегабайтах.
    max_image_mb: int = 5
    max_attachment_mb: int = 20
    # Сколько картинок и сколько файлов можно прикрепить к одной задаче.
    max_files_per_kind: int = 10

    # Сколько секунд живут QR-ключ и код из бота.
    login_ttl_seconds: int = 300
    # Сколько попыток ввести код можно сделать за минуту с одного адреса.
    code_attempts_per_minute: int = 5


settings = Settings()
