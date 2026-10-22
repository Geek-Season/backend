"""Уведомления: текст по событию, запись в базу, отправка в Telegram.

Без дублей: у уведомления уникальный event_id. Если RabbitMQ доставит то же
событие повторно (так бывает при сбоях — гарантия «хотя бы один раз»),
вторая строка не создастся и второе сообщение не уйдёт.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import telegram
from app.modules.auth.models import User
from app.modules.notifications.models import Notification

Sender = Callable[[int, str], None]


def render(event: dict) -> str | None:
    """Текст уведомления по событию или None, если о событии не сообщаем."""
    kind = event["event"]
    if kind == "achievement.granted":
        return f"{event['icon']} Новое достижение: <b>{event['title']}</b>\n{event['description']}"
    if kind == "exam.finished":
        approx = "≈" if event.get("test_score_approximate") else ""
        verdict = "✅ порог для вузов пройден" if event["test_score"] >= 46 else (
            "✅ порог 40 пройден" if event["test_score"] >= 40 else "❌ ниже порога 40"
        )
        return (
            f"📝 Пробник завершён: <b>{approx}{event['test_score']}</b> тестовых баллов "
            f"({event['primary_score']} из {event['max_score']} первичных)\n{verdict}\n"
            "Разбор — на сайте в разделе «Пробник»."
        )
    return None


def notify(db: Session, event: dict, send: Sender = telegram.send_message) -> Notification | None:
    """Обработать событие. Временная ошибка Telegram — исключение (воркер повторит)."""
    text = render(event)
    if text is None:
        return None
    if db.scalar(select(Notification.id).where(Notification.event_id == event["event_id"])):
        return None  # уже обработано — повторная доставка того же события

    user = db.get(User, event["user_id"])
    if user is None:
        return None  # ученика удалили — сообщать некому

    notification = Notification(user_id=user.id, event_id=event["event_id"], kind=event["event"], text=text)
    try:
        send(user.telegram_id, text)
        notification.sent_at = datetime.now(UTC)
    except telegram.TelegramPermanentError as error:
        notification.error = str(error)[:300]  # повтор не поможет — запоминаем причину
    # TelegramTemporaryError не ловим: транзакция откатится, воркер повторит позже.
    db.add(notification)
    db.commit()
    return notification


def list_for_user(db: Session, user_id: int, limit: int = 20) -> list[Notification]:
    return list(db.scalars(
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    ))
