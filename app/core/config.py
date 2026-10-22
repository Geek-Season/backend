"""Настройки приложения.

Значения берутся из переменных окружения или файла .env.
Так один и тот же код работает и на твоём компьютере, и в Docker:
меняется только адрес базы, а не код.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Адрес базы: postgresql+psycopg://пользователь:пароль@хост:порт/имя_базы
    database_url: str = "postgresql+psycopg://geek:geek@localhost:5432/geek_season"


settings = Settings()
