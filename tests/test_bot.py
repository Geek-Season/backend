"""Telegram-бот без aiogram: команды, кнопки входа по QR, цикл getUpdates, ошибки Bot API."""

import http.server
import json
import threading

import pytest

from app.bot import __main__ as bot_main
from app.bot.handlers import handle_update
from app.core import telegram
from app.core.config import settings
from app.modules.auth import service

USER = {"id": 777, "first_name": "Вася", "username": "vasya"}
CHAT = {"id": 777, "type": "private"}


class FakeApi:
    """Вместо Telegram: запоминает, что бот отправил."""

    def __init__(self):
        self.sent, self.edited, self.answered = [], [], []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    def edit_message_text(self, chat_id, message_id, text):
        self.edited.append({"chat_id": chat_id, "message_id": message_id, "text": text})

    def answer_callback_query(self, callback_query_id):
        self.answered.append(callback_query_id)


def message(text, user=USER):
    return {"update_id": 1, "message": {"message_id": 10, "chat": CHAT, "from": user, "text": text}}


def callback(data, user=USER):
    return {"update_id": 2, "callback_query": {
        "id": "cb1", "from": user, "data": data,
        "message": {"message_id": 11, "chat": CHAT},
    }}


@pytest.fixture
def api():
    return FakeApi()


# ---------- Команды ----------

def test_start_plain_greets_with_escaped_name(api, fake_redis, db):
    handle_update(api, fake_redis, db, message("/start", user={**USER, "first_name": "<b>Хакер</b>"}))
    text = api.sent[0]["text"]
    assert "Привет, &lt;b&gt;Хакер&lt;/b&gt;!" in text  # имя не ломает HTML-разметку сообщения
    assert "/login" in text


def test_start_with_token_asks_confirmation(api, fake_redis, db):
    token = service.start_qr_login(fake_redis)
    handle_update(api, fake_redis, db, message(f"/start {token}"))
    sent = api.sent[0]
    assert "Войти на сайт" in sent["text"]
    buttons = sent["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in buttons] == [f"qr:yes:{token}", f"qr:no:{token}"]


def test_start_with_expired_token(api, fake_redis, db):
    handle_update(api, fake_redis, db, message("/start nope"))
    assert "устарела" in api.sent[0]["text"]
    assert api.sent[0]["reply_markup"] is None


def test_command_with_bot_name(api, fake_redis, db):
    handle_update(api, fake_redis, db, message("/START@GeekSeasonBot"))
    assert "Привет" in api.sent[0]["text"]


def test_login_issues_code_that_works_on_site(api, fake_redis, db, client):
    handle_update(api, fake_redis, db, message("/login"))
    text = api.sent[0]["text"]
    code = text.split("<code>")[1].split("</code>")[0]
    assert len(code) == 6 and code.isdigit()
    response = client.post("/auth/code", json={"code": code})
    assert response.status_code == 200
    assert response.json()["user"]["username"] == "vasya"


def test_unknown_messages_ignored(api, fake_redis, db):
    handle_update(api, fake_redis, db, message("привет"))
    handle_update(api, fake_redis, db, {"update_id": 5, "edited_message": {}})
    handle_update(api, fake_redis, db, {"update_id": 6, "message": {"chat": CHAT, "from": USER, "photo": []}})
    assert api.sent == []


# ---------- Кнопки входа по QR ----------

def test_yes_confirms_login(api, fake_redis, db, client):
    token = service.start_qr_login(fake_redis)
    handle_update(api, fake_redis, db, callback(f"qr:yes:{token}"))
    assert "Готово" in api.edited[0]["text"]
    assert api.answered == ["cb1"]
    status = client.get(f"/auth/telegram/status/{token}").json()
    assert status["status"] == "confirmed" and status["access_token"]


def test_no_declines_login(api, fake_redis, db, client):
    token = service.start_qr_login(fake_redis)
    handle_update(api, fake_redis, db, callback(f"qr:no:{token}"))
    assert api.edited[0]["text"] == "Вход отменён."
    assert client.get(f"/auth/telegram/status/{token}").json()["status"] == "declined"


def test_yes_on_expired_link(api, fake_redis, db):
    handle_update(api, fake_redis, db, callback("qr:yes:old"))
    assert "устарела" in api.edited[0]["text"]
    assert api.answered == ["cb1"]


def test_unknown_callback_just_answered(api, fake_redis, db):
    handle_update(api, fake_redis, db, callback("something"))
    assert api.answered == ["cb1"] and api.edited == []


# ---------- Цикл getUpdates ----------

def test_poll_once_handles_updates_and_moves_offset(monkeypatch, api, fake_redis):
    calls = []
    updates = [message("/start") | {"update_id": 41}, message("/oops") | {"update_id": 42}]

    def fake_call(method, payload=None, timeout=10):
        calls.append((method, payload))
        return updates

    monkeypatch.setattr(bot_main.telegram, "call", fake_call)
    monkeypatch.setattr(bot_main, "redis_client", fake_redis)
    assert bot_main.poll_once(api, None) == 43
    assert calls[0][0] == "getUpdates" and "offset" not in calls[0][1]
    assert len(api.sent) == 1  # /start обработан, /oops пропущен
    assert bot_main.poll_once(api, 43) == 43
    assert calls[1][1]["offset"] == 43


def test_poll_once_survives_handler_error(monkeypatch, api, fake_redis):
    monkeypatch.setattr(bot_main.telegram, "call", lambda *a, **k: [message("/start") | {"update_id": 7}])
    monkeypatch.setattr(bot_main, "redis_client", fake_redis)

    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_main, "handle_update", broken)
    assert bot_main.poll_once(api, None) == 8  # сломанное обновление пропущено, бот работает дальше


# ---------- Bot API: ответы и ошибки ----------

@pytest.fixture
def fake_telegram(monkeypatch):
    """Поддельный сервер Bot API: путь /bot<токен>/<метод> → (код, тело)."""
    replies = {}
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            method = self.path.rsplit("/", 1)[-1]
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            requests.append((method, body))
            code, reply = replies.get(method, (200, {"ok": True, "result": True}))
            data = json.dumps(reply).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(settings, "telegram_api_url", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setattr(settings, "bot_token", "123:test")
    yield replies, requests
    server.shutdown()


def test_call_returns_result(fake_telegram):
    replies, requests = fake_telegram
    replies["getMe"] = (200, {"ok": True, "result": {"username": "GeekSeasonBot"}})
    assert telegram.call("getMe") == {"username": "GeekSeasonBot"}
    telegram.send_message(1, "<b>hi</b>", {"inline_keyboard": []})
    assert requests[-1] == ("sendMessage", {
        "chat_id": 1, "text": "<b>hi</b>", "parse_mode": "HTML", "reply_markup": {"inline_keyboard": []},
    })


@pytest.mark.parametrize("code, error", [
    (401, telegram.TelegramPermanentError),   # неверный токен
    (404, telegram.TelegramPermanentError),   # так Telegram отвечает на несуществующий токен
    (403, telegram.TelegramPermanentError),   # бот заблокирован учеником
    (409, telegram.TelegramTemporaryError),   # этот токен опрашивает кто-то ещё
    (429, telegram.TelegramTemporaryError),
    (502, telegram.TelegramTemporaryError),
])
def test_call_error_kinds(fake_telegram, code, error):
    replies, _ = fake_telegram
    replies["getMe"] = (code, {"ok": False, "description": "error"})
    with pytest.raises(error):
        telegram.call("getMe")


def test_call_network_error_is_temporary(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_url", "http://127.0.0.1:9")  # порт, где никого нет
    monkeypatch.setattr(settings, "bot_token", "123:test")
    with pytest.raises(telegram.TelegramTemporaryError):
        telegram.call("getMe", timeout=2)


def test_no_aiogram_in_bot():
    """Бот не тянет aiogram (~170 МБ памяти) — иначе на хостинге его снова убьют."""
    import subprocess
    import sys

    code = "import sys, app.bot.__main__; print('aiogram' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"
