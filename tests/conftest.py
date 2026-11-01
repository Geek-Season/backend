"""Подготовка к тестам.

Тесты работают с ОТДЕЛЬНОЙ базой (geek_test), чтобы не портить данные разработки.
Адрес задаётся переменной TEST_DATABASE_URL.
Перед каждым тестом база очищается и заново наполняется из app/seed.py.

Вместо настоящего Redis используется fakeredis — его копия в памяти,
поэтому для тестов Redis запускать не нужно.
"""

import os

os.environ["RABBITMQ_URL"] = ""  # в тестах брокер выключен; события ловит фикстура events
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://geek:geek@localhost:5433/geek_test"
)

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    seed()
    yield


@pytest.fixture(autouse=True)
def upload_dir(tmp_path, monkeypatch):
    """Загруженные в тестах файлы — во временную папку, а не в рабочую uploads/."""
    from app.core.config import settings

    path = tmp_path / "uploads"
    monkeypatch.setattr(settings, "upload_dir", str(path))
    return path


@pytest.fixture
def fake_redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    app.dependency_overrides[get_redis] = lambda: r
    yield r
    app.dependency_overrides.pop(get_redis, None)


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client(fake_redis) -> TestClient:
    return TestClient(app)


@pytest.fixture
def admin_client(fake_redis, db) -> TestClient:
    """Клиент, вошедший как администратор (может управлять банком задач)."""
    from app.modules.auth.models import ROLE_ADMIN
    from app.modules.auth.service import TelegramProfile, upsert_user
    from app.modules.auth.tokens import create_access_token

    admin = upsert_user(db, TelegramProfile(telegram_id=1, first_name="Админ"))
    admin.role = ROLE_ADMIN
    db.commit()
    return TestClient(app, headers={"Authorization": f"Bearer {create_access_token(admin.id)}"})


@pytest.fixture
def new_task(admin_client):
    """Создаёт задачу в номере 1 и возвращает её JSON."""
    response = admin_client.post(
        "/tasks",
        json={"exam_number": "1", "statement": "Тестовая задача", "correct_answer": "42"},
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def login(client, fake_redis, db):
    """login(telegram_id, имя) → заголовки с токеном вошедшего ученика."""
    from app.modules.auth import service as auth_service

    def _login(telegram_id: int = 111, name: str = "Вася") -> dict:
        profile = auth_service.TelegramProfile(telegram_id=telegram_id, first_name=name)
        code = auth_service.issue_code(fake_redis, db, profile)
        token = client.post("/auth/code", json={"code": code}).json()["access_token"]
        for key in fake_redis.scan_iter("login:attempts:*"):
            fake_redis.delete(key)  # сбросить счётчик попыток ввода кода
        return {"Authorization": f"Bearer {token}"}

    return _login


@pytest.fixture
def ranked(db):
    """ranked(user_id, ранг, PTS) — выдать ученику ранг напрямую (как после входного пробника):
    в таблицу лидеров попадают только ученики с рангом."""
    from datetime import UTC, datetime

    from app.modules.auth.models import User

    def _ranked(user_id: int, rank: str = "hero", pts: float = 1200.0) -> None:
        user = db.get(User, user_id)
        user.rank_key, user.pts, user.calibrated_at = rank, pts, datetime.now(UTC)
        db.commit()

    return _ranked


@pytest.fixture
def events(monkeypatch):
    """Список опубликованных событий [(event, payload), ...] вместо настоящего RabbitMQ."""
    from app.core import events as events_module

    published = []
    monkeypatch.setattr(events_module, "publish", lambda event, payload: published.append((event, payload)) or True)
    return published
