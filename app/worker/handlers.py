"""Что воркер делает с событием из каждой очереди.

Обработчик получает событие (dict) и открытую сессию базы. Исключение =
«не получилось» — обвязка в process.py повторит позже, а после нескольких
неудач отправит сообщение в очередь «мёртвых писем».
"""

from sqlalchemy.orm import Session

from app.core import events
from app.modules.achievements import service as achievements
from app.modules.notifications import service as notifications


def handle_achievements(event: dict, db: Session) -> None:
    """task.answered / exam.finished → пересчитать достижения ученика."""
    for achievement in achievements.check_and_grant(db, event["user_id"]):
        events.publish("achievement.granted", {
            "user_id": event["user_id"],
            "code": achievement.code,
            "icon": achievement.icon,
            "title": achievement.title,
            "description": achievement.description,
        })


def handle_notifications(event: dict, db: Session) -> None:
    """exam.finished / achievement.granted → уведомление в Telegram."""
    notifications.notify(db, event)


HANDLERS = {
    "achievements": handle_achievements,
    "notifications": handle_notifications,
}
