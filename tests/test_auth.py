"""Вход через Telegram.

Бот здесь не нужен: тест вызывает те же функции сервиса, что и бот,
когда человек нажимает «Да» или отправляет /login.
"""

import pytest

from app.core.config import settings
from app.modules.auth import service
from app.modules.auth.service import TelegramProfile

VASYA = TelegramProfile(telegram_id=111, first_name="Вася", username="vasya")
PETYA = TelegramProfile(telegram_id=222, first_name="Петя")


@pytest.fixture(autouse=True)
def bot_name(monkeypatch):
    monkeypatch.setattr(settings, "bot_username", "GeekSeasonTestBot")


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------- QR ----------

def test_qr_full_flow(client, fake_redis, db):
    start = client.post("/auth/telegram/start").json()
    token = start["token"]
    assert start["bot_link"] == f"https://t.me/GeekSeasonTestBot?start={token}"
    assert client.get(f"/auth/telegram/status/{token}").json()["status"] == "pending"

    # Человек нажал «Да» в боте.
    assert service.confirm_qr_login(fake_redis, db, token, VASYA) is True

    result = client.get(f"/auth/telegram/status/{token}").json()
    assert result["status"] == "confirmed"
    assert result["user"]["first_name"] == "Вася"

    me = client.get("/auth/me", headers=auth(result["access_token"]))
    assert me.status_code == 200
    assert me.json()["username"] == "vasya"

    # Ключ одноразовый: второй браузер по нему уже не войдёт.
    assert client.get(f"/auth/telegram/status/{token}").json() == {
        "status": "expired", "access_token": None, "user": None,
    }


def test_qr_declined(client, fake_redis):
    token = client.post("/auth/telegram/start").json()["token"]
    assert service.decline_qr_login(fake_redis, token) is True
    assert client.get(f"/auth/telegram/status/{token}").json()["status"] == "declined"


def test_qr_cannot_confirm_twice_or_after_decline(client, fake_redis, db):
    token = client.post("/auth/telegram/start").json()["token"]
    assert service.confirm_qr_login(fake_redis, db, token, VASYA) is True
    # Петя открыл ту же ссылку (например, её переслали) — подтверждение не перезапишется.
    assert service.confirm_qr_login(fake_redis, db, token, PETYA) is False

    token2 = client.post("/auth/telegram/start").json()["token"]
    service.decline_qr_login(fake_redis, token2)
    assert service.confirm_qr_login(fake_redis, db, token2, VASYA) is False


def test_qr_unknown_or_expired_token(client, fake_redis, db):
    assert client.get("/auth/telegram/status/no-such-token").json()["status"] == "expired"

    token = client.post("/auth/telegram/start").json()["token"]
    fake_redis.delete(f"login:qr:{token}")  # как будто прошло 5 минут
    assert client.get(f"/auth/telegram/status/{token}").json()["status"] == "expired"
    assert service.confirm_qr_login(fake_redis, db, token, VASYA) is False


def test_qr_key_has_ttl(client, fake_redis):
    token = client.post("/auth/telegram/start").json()["token"]
    assert 0 < fake_redis.ttl(f"login:qr:{token}") <= settings.login_ttl_seconds


def test_start_without_bot_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "bot_username", "")
    assert client.post("/auth/telegram/start").status_code == 503


# ---------- Код ----------

def test_code_login(client, fake_redis, db):
    code = service.issue_code(fake_redis, db, VASYA)
    assert len(code) == 6 and code.isdigit()

    response = client.post("/auth/code", json={"code": code})
    assert response.status_code == 200
    assert response.json()["user"]["first_name"] == "Вася"

    # Код одноразовый.
    assert client.post("/auth/code", json={"code": code}).status_code == 400


def test_new_code_cancels_old(client, fake_redis, db):
    old = service.issue_code(fake_redis, db, VASYA)
    new = service.issue_code(fake_redis, db, VASYA)
    assert client.post("/auth/code", json={"code": old}).status_code == 400
    assert client.post("/auth/code", json={"code": new}).status_code == 200


def test_code_format(client):
    for bad in ["12345", "1234567", "abcdef", ""]:
        assert client.post("/auth/code", json={"code": bad}).status_code == 422


def test_code_bruteforce_limit(client, fake_redis, db):
    for _ in range(settings.code_attempts_per_minute):
        assert client.post("/auth/code", json={"code": "000000"}).status_code == 400
    # Даже правильный код не поможет, пока не пройдёт минута.
    code = service.issue_code(fake_redis, db, VASYA)
    assert client.post("/auth/code", json={"code": code}).status_code == 429


def test_qr_start_limit(client, fake_redis):
    """Не больше QR_STARTS_PER_MINUTE ключей в минуту с одного адреса — защита от спама ключами."""
    for _ in range(settings.qr_starts_per_minute):
        assert client.post("/auth/telegram/start").status_code == 200
    response = client.post("/auth/telegram/start")
    assert response.status_code == 429
    assert response.json()["detail"] == "Слишком много попыток. Подождите минуту"
    assert response.headers["retry-after"] == "60"
    # Другой адрес (другой ученик) не заблокирован.
    assert service.start_qr_login(fake_redis, "1.2.3.4")


def test_qr_start_limit_resets_after_minute(fake_redis):
    for _ in range(settings.qr_starts_per_minute):
        service.start_qr_login(fake_redis, "5.6.7.8")
    with pytest.raises(service.TooManyAttempts):
        service.start_qr_login(fake_redis, "5.6.7.8")
    assert 0 < fake_redis.ttl("login:qr_starts:5.6.7.8") <= 60  # счётчик сам исчезнет через минуту
    fake_redis.delete("login:qr_starts:5.6.7.8")  # «прошла минута»
    assert service.start_qr_login(fake_redis, "5.6.7.8")


def test_rate_counter_without_ttl_gets_one(fake_redis):
    """Если срок счётчика не выставился (сбой между командами), адрес не блокируется навсегда."""
    fake_redis.set("login:qr_starts:9.9.9.9", 3)  # счётчик без срока
    service.start_qr_login(fake_redis, "9.9.9.9")
    assert 0 < fake_redis.ttl("login:qr_starts:9.9.9.9") <= 60


# ---------- Токен и пользователи ----------

def test_me_requires_valid_token(client):
    assert client.get("/auth/me").status_code == 401
    assert client.get("/auth/me", headers=auth("garbage")).status_code == 401


def test_forged_token_rejected(client, fake_redis, db):
    code = service.issue_code(fake_redis, db, VASYA)
    token = client.post("/auth/code", json={"code": code}).json()["access_token"]
    head, payload, signature = token.split(".")
    forged = f"{head}.{payload}.{signature[:-2]}xx"
    assert client.get("/auth/me", headers=auth(forged)).status_code == 401


def test_same_telegram_user_not_duplicated(fake_redis, db):
    first = service.upsert_user(db, VASYA)
    renamed = TelegramProfile(telegram_id=111, first_name="Василий", username="vasiliy")
    second = service.upsert_user(db, renamed)
    assert first.id == second.id
    assert second.first_name == "Василий"
