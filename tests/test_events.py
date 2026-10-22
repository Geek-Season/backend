"""События, достижения, уведомления и обработка сообщений воркером (без настоящего RabbitMQ)."""

import json
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pika
import pytest
from sqlalchemy import select, update

from app.core import telegram
from app.core.config import settings
from app.modules.achievements import service as achievements
from app.modules.achievements.models import UserAchievement
from app.modules.exam.models import ExamSession
from app.modules.notifications import service as notifications
from app.modules.notifications.models import Notification
from app.modules.progress.models import Attempt
from app.modules.tasks.models import Task
from app.worker import handlers
from app.worker.process import MAX_RETRIES, process

TASK_17 = 8


def user_id(client, headers):
    return client.get("/auth/me", headers=headers).json()["id"]


def codes(db, uid):
    return set(db.scalars(select(UserAchievement.achievement_code).where(UserAchievement.user_id == uid)))


# ---------- Публикация событий из API ----------

def test_answer_publishes_event(client, login, events):
    me = login()
    client.post(f"/tasks/{TASK_17}/check", json={"answer": "2 20"}, headers=me)
    client.post(f"/tasks/{TASK_17}/check", json={"answer": "2 20"}, headers=me)
    assert [e for e, _ in events] == ["task.answered", "task.answered"]
    assert events[0][1] | {} == {"user_id": user_id(client, me), "task_id": TASK_17, "is_correct": True, "first_solve": True}
    assert events[1][1]["first_solve"] is False


def test_guest_answer_publishes_nothing(client, events):
    client.post(f"/tasks/{TASK_17}/check", json={"answer": "2 20"})
    assert events == []


def test_exam_finish_publishes_event(client, login, events, db):
    me = login()
    exam = client.post("/exam/start", headers=me).json()
    client.post(f"/exam/{exam['id']}/finish", headers=me)
    client.post(f"/exam/{exam['id']}/finish", headers=me)  # повтор — второго события нет
    finished = [p for e, p in events if e == "exam.finished"]
    assert len(finished) == 1
    assert finished[0]["test_score"] == 0 and finished[0]["max_score"] == exam["max_score"]


def test_exam_auto_finish_publishes_event(client, login, events, db):
    me = login()
    exam = client.post("/exam/start", headers=me).json()
    db.execute(update(ExamSession).where(ExamSession.id == exam["id"])
               .values(deadline_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()
    client.get("/exam/current", headers=me)  # первое обращение после срока завершает пробник
    assert [e for e, _ in events] == ["exam.finished"]


def test_api_works_when_broker_is_down(client, login, monkeypatch):
    """Брокер недоступен: publish вернёт False, но ответ ученику — как обычно."""
    monkeypatch.setattr(settings, "rabbitmq_url", "amqp://geek:geek@127.0.0.1:1/")  # там никого нет
    response = client.post(f"/tasks/{TASK_17}/check", json={"answer": "2 20"}, headers=login())
    assert response.status_code == 200 and response.json()["is_correct"] is True


# ---------- Достижения ----------

def test_first_solve_and_idempotency(client, login, db):
    me = login()
    uid = user_id(client, me)
    assert achievements.check_and_grant(db, uid) == []
    client.post(f"/tasks/{TASK_17}/check", json={"answer": "2 20"}, headers=me)
    assert [a.code for a in achievements.check_and_grant(db, uid)] == ["first_solve"]
    assert achievements.check_and_grant(db, uid) == []  # повтор ничего не выдаёт
    assert codes(db, uid) == {"first_solve"}


def test_streak_and_count_achievements(client, login, db):
    me = login()
    uid = user_id(client, me)
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()
    task_ids = list(db.scalars(select(Task.id).order_by(Task.id)))[:10]
    for i, task_id in enumerate(task_ids):  # 10 разных задач за последние 3 дня
        moment = datetime.combine(today - timedelta(days=i % 3), time(12), tzinfo=tz)
        db.add(Attempt(user_id=uid, task_id=task_id, answer="x", is_correct=True, created_at=moment))
    db.commit()
    granted = {a.code for a in achievements.check_and_grant(db, uid)}
    assert granted == {"first_solve", "solved_10", "streak_3"}


def test_exam_achievements(client, login, db):
    me = login()
    uid = user_id(client, me)
    exam = client.post("/exam/start", headers=me).json()
    client.post(f"/exam/{exam['id']}/finish", headers=me)
    assert {a.code for a in achievements.check_and_grant(db, uid)} == {"first_exam"}
    # «Как будто» набрал 21 первичный = 80 тестовых.
    db.execute(update(ExamSession).where(ExamSession.id == exam["id"]).values(primary_score=21))
    db.commit()
    assert {a.code for a in achievements.check_and_grant(db, uid)} == {"exam_70"}


def test_number_closed(client, login, db, admin_client):
    me = login()
    uid = user_id(client, me)
    ids = [admin_client.post("/tasks", json={"exam_number": "1", "statement": f"З{i}", "correct_answer": "1"}).json()["id"]
           for i in range(3)]
    for task_id in ids[:2]:
        client.post(f"/tasks/{task_id}/check", json={"answer": "1"}, headers=me)
    assert "number_closed" not in {a.code for a in achievements.check_and_grant(db, uid)}
    client.post(f"/tasks/{ids[2]}/check", json={"answer": "1"}, headers=me)
    assert "number_closed" in {a.code for a in achievements.check_and_grant(db, uid)}


def test_achievements_api(client, login, db):
    me = login()
    client.post(f"/tasks/{TASK_17}/check", json={"answer": "2 20"}, headers=me)
    achievements.check_and_grant(db, user_id(client, me))
    items = client.get("/me/achievements", headers=me).json()
    assert len(items) == 8
    assert [i["code"] for i in items if i["received_at"]] == ["first_solve"]
    assert client.get("/me/achievements").status_code == 401


def test_achievements_handler_publishes_granted(client, login, db, events):
    me = login()
    client.post(f"/tasks/{TASK_17}/check", json={"answer": "2 20"}, headers=me)
    events.clear()
    event = {"event": "task.answered", "event_id": "e1", "user_id": user_id(client, me)}
    handlers.handle_achievements(event, db)
    handlers.handle_achievements(event, db)  # повторная доставка — второго «granted» нет
    assert [(e, p["code"]) for e, p in events] == [("achievement.granted", "first_solve")]


# ---------- Уведомления ----------

def granted_event(uid, event_id="ev1"):
    return {"event": "achievement.granted", "event_id": event_id, "user_id": uid,
            "code": "first_solve", "icon": "🎯", "title": "Первый шаг", "description": "Решить первую задачу"}


def test_notification_sent_once(client, login, db):
    me = login()
    uid = user_id(client, me)
    sent = []
    notifications.notify(db, granted_event(uid), send=lambda chat, text: sent.append((chat, text)))
    notifications.notify(db, granted_event(uid), send=lambda chat, text: sent.append((chat, text)))
    assert len(sent) == 1 and sent[0][0] == 111 and "Первый шаг" in sent[0][1]
    stored = client.get("/me/notifications", headers=me).json()
    assert len(stored) == 1 and stored[0]["sent_at"] is not None


def test_notification_temporary_error_is_retried(client, login, db):
    uid = user_id(client, login())

    def broken(chat, text):
        raise telegram.TelegramTemporaryError("timeout")

    with pytest.raises(telegram.TelegramTemporaryError):
        notifications.notify(db, granted_event(uid), send=broken)
    db.rollback()
    assert db.scalar(select(Notification.id)) is None  # ничего не записали — воркер повторит
    notifications.notify(db, granted_event(uid), send=lambda c, t: None)
    assert db.scalar(select(Notification.sent_at)) is not None


def test_notification_permanent_error_is_stored(client, login, db):
    uid = user_id(client, login())

    def blocked(chat, text):
        raise telegram.TelegramPermanentError("403: bot was blocked by the user")

    notification = notifications.notify(db, granted_event(uid), send=blocked)
    assert notification.sent_at is None and "blocked" in notification.error


def test_exam_notification_text():
    text = notifications.render({"event": "exam.finished", "test_score": 56, "test_score_approximate": True,
                                 "primary_score": 12, "max_score": 18})
    assert "≈56" in text and "12 из 18" in text and "вузов" in text


# ---------- Обвязка воркера: повторы и «мёртвые письма» ----------

class FakeChannel:
    def __init__(self):
        self.acked, self.nacked, self.republished = [], [], []

    def basic_ack(self, tag):
        self.acked.append(tag)

    def basic_nack(self, tag, requeue):
        self.nacked.append((tag, requeue))

    def basic_publish(self, exchange, routing_key, body, properties):
        self.republished.append((exchange, routing_key, properties.headers))


def run(handler, retries=0, body=b'{"event": "x", "user_id": 1}'):
    channel = FakeChannel()
    props = pika.BasicProperties(headers={"x-retries": retries} if retries else None, type="x")
    result = process(channel, "achievements", SimpleNamespace(delivery_tag=7), props, body, handler,
                     session_factory=lambda: _NullSession())
    return result, channel


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_process_ok():
    result, channel = run(lambda event, db: None)
    assert result == "ok" and channel.acked == [7] and not channel.republished


def test_process_retries_then_dead():
    def fail(event, db):
        raise RuntimeError("boom")

    result, channel = run(fail)
    assert result == "retry"
    assert channel.republished == [("", "achievements.retry", {"x-retries": 1})]  # в очередь ожидания
    assert channel.acked == [7]

    result, channel = run(fail, retries=MAX_RETRIES)
    assert result == "dead" and channel.nacked == [(7, False)]  # → geek.dead


def test_process_bad_json_goes_dead_immediately():
    result, channel = run(lambda e, db: None, body=b"not json")
    assert result == "dead"
