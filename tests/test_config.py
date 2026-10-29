"""Настройки для сервера: обязательный JWT_SECRET в production и CORS для сайта на другом адресе."""

import importlib

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import DEV_JWT_SECRET, Settings

STRONG = "x" * 48


@pytest.mark.parametrize("secret", [DEV_JWT_SECRET, "", "short-secret"])
def test_production_requires_own_jwt_secret(secret):
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        Settings(app_env="production", jwt_secret=secret, _env_file=None)


def test_production_with_strong_secret_ok():
    assert Settings(app_env="production", jwt_secret=STRONG, _env_file=None).jwt_secret == STRONG


def test_development_allows_default_secret():
    assert Settings(app_env="development", _env_file=None).jwt_secret == DEV_JWT_SECRET


def test_production_secret_from_environment(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
    monkeypatch.setenv("JWT_SECRET", STRONG)
    assert Settings(_env_file=None).app_env == "production"


def test_cors_origin_list():
    s = Settings(cors_origins=" https://geek-season.github.io/ ,https://example.org", _env_file=None)
    assert s.cors_origin_list == ["https://geek-season.github.io", "https://example.org"]
    assert Settings(_env_file=None).cors_origin_list == []


@pytest.fixture
def cors_app(monkeypatch):
    """Приложение, собранное с CORS_ORIGINS (main.py читает настройку при импорте)."""
    import app.main as main
    from app.core import config

    monkeypatch.setattr(config.settings, "cors_origins", "https://geek-season.github.io")
    yield importlib.reload(main).app
    monkeypatch.undo()
    importlib.reload(main)


def test_cors_allows_site(cors_app, fake_redis):
    client = TestClient(cors_app)
    origin = "https://geek-season.github.io"
    preflight = client.options("/tasks/1/check", headers={
        "Origin": origin,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    response = client.get("/numbers", headers={"Origin": origin})
    assert response.headers["access-control-allow-origin"] == origin


def test_cors_blocks_other_sites(cors_app, fake_redis):
    response = TestClient(cors_app).get("/numbers", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200  # сам запрос работает, но браузер не отдаст ответ чужому сайту
    assert "access-control-allow-origin" not in response.headers


def test_no_cors_by_default(client):
    response = client.get("/numbers", headers={"Origin": "https://geek-season.github.io"})
    assert "access-control-allow-origin" not in response.headers
