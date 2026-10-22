"""Обвязка обработки одного сообщения: подтверждение, повторы, «мёртвые письма».

Правила:
- обработали — ack (RabbitMQ удаляет сообщение из очереди);
- не получилось — кладём копию с x-retries + 1 в очередь ожидания <очередь>.retry,
  исходное подтверждаем; через RETRY_DELAY_MS копия сама вернётся в основную очередь;
- после MAX_RETRIES неудач — nack без возврата: RabbitMQ перекладывает
  сообщение в очередь geek.dead (через dead-letter exchange), где его
  можно посмотреть в веб-панели и разобраться.
"""

import json
import logging
from collections.abc import Callable

import pika
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.events import retry_queue

log = logging.getLogger(__name__)

MAX_RETRIES = 3


def process(
    channel,
    queue: str,
    method,
    properties: pika.BasicProperties,
    body: bytes,
    handler: Callable[[dict, Session], None],
    session_factory: Callable[[], Session] = SessionLocal,
) -> str:
    """Обработать сообщение. Возвращает итог: ok | retry | dead (для логов и тестов)."""
    retries = int((properties.headers or {}).get("x-retries", 0))
    try:
        event = json.loads(body)
        with session_factory() as db:
            handler(event, db)
    except Exception as error:  # noqa: BLE001 — любая ошибка обработчика
        if retries < MAX_RETRIES and not isinstance(error, json.JSONDecodeError):
            log.warning("%s: ошибка (%s), повтор %d из %d", queue, error, retries + 1, MAX_RETRIES)
            headers = dict(properties.headers or {}, **{"x-retries": retries + 1})
            retry_props = pika.BasicProperties(
                content_type=properties.content_type,
                delivery_mode=pika.DeliveryMode.Persistent,
                message_id=properties.message_id,
                type=properties.type,
                headers=headers,
            )
            # Через exchange по умолчанию ("") — прямо в очередь ожидания этой очереди.
            channel.basic_publish("", routing_key=retry_queue(queue), body=body, properties=retry_props)
            channel.basic_ack(method.delivery_tag)
            return "retry"
        log.error("%s: сообщение не обработано, отправлено в geek.dead: %s", queue, error)
        channel.basic_nack(method.delivery_tag, requeue=False)
        return "dead"
    channel.basic_ack(method.delivery_tag)
    return "ok"
