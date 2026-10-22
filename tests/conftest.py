"""Подготовка к тестам.

Тесты работают с ОТДЕЛЬНОЙ базой (geek_test), чтобы не портить данные разработки.
Адрес задаётся переменной TEST_DATABASE_URL.
Перед каждым тестом база очищается и заново наполняется из app/seed.py.

Вместо настоящего Redis используется fakeredis — его копия в памяти,
поэтому для тестов Redis запускать не нужно.
"""

import os

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
def new_task(client):
    """Создаёт задачу в номере 1 и возвращает её JSON."""
    response = client.post(
        "/tasks",
        json={"exam_number": "1", "statement": "Тестовая задача", "correct_answer": "42"},
    )
    assert response.status_code == 201
    return response.json()
