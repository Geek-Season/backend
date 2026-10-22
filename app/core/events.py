"""События и брокер сообщений RabbitMQ.

API не делает «тяжёлую» и ненадёжную работу сам (выдача достижений, отправка
в Telegram), а публикует СОБЫТИЕ в брокер. Фоновый воркер (app/worker) его
забирает и обрабатывает. Так ответ ученику не ждёт Telegram, а если бот
временно недоступен — сообщения дождутся его в очереди.

Схема (topology):

    exchange geek.events (topic)
      task.answered        → очередь achievements
      exam.finished        → очереди achievements и notifications
      achievement.granted  → очередь notifications

    очереди achievements.retry, notifications.retry — «зал ожидания» для повторов:
      сообщение лежит там RETRY_DELAY_MS и само возвращается в основную очередь.

    exchange geek.dlx (fanout) → очередь geek.dead  — «мёртвые письма»:
      сообщения, которые не удалось обработать после нескольких повторов.

Публикация НИКОГДА не роняет запрос: если брокер недоступен, событие
пишется в лог и теряется. Это безопасно — воркер не доверяет содержимому
события, а пересчитывает всё из базы, поэтому следующее событие того же
ученика исправит пропуск.
"""

import json
import logging
import threading
import uuid
from datetime import UTC, datetime

import pika
from pika.adapters.blocking_connection import BlockingChannel

from app.core.config import settings

log = logging.getLogger(__name__)

EXCHANGE = "geek.events"
DLX = "geek.dlx"
DEAD_QUEUE = "geek.dead"
RETRY_DELAY_MS = 5000  # пауза перед повтором: временный сбой Telegram успеет пройти

# очередь → какие события в неё попадают
QUEUES = {
    "achievements": ["task.answered", "exam.finished"],
    "notifications": ["exam.finished", "achievement.granted"],
}


def retry_queue(queue: str) -> str:
    return f"{queue}.retry"


def declare_topology(channel: BlockingChannel) -> None:
    """Создать exchange'и и очереди (повторный вызов ничего не ломает)."""
    channel.exchange_declare(EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(DLX, exchange_type="fanout", durable=True)
    channel.queue_declare(DEAD_QUEUE, durable=True)
    channel.queue_bind(DEAD_QUEUE, DLX)
    for queue, keys in QUEUES.items():
        channel.queue_declare(queue, durable=True, arguments={"x-dead-letter-exchange": DLX})
        for key in keys:
            channel.queue_bind(queue, EXCHANGE, routing_key=key)
        # Очередь ожидания: без потребителей; по истечении TTL сообщение «умирает»
        # и через exchange по умолчанию возвращается в основную очередь.
        channel.queue_declare(retry_queue(queue), durable=True, arguments={
            "x-message-ttl": RETRY_DELAY_MS,
            "x-dead-letter-exchange": "",
            "x-dead-letter-routing-key": queue,
        })


def make_message(event: str, payload: dict) -> tuple[bytes, pika.BasicProperties]:
    body = {"event": event, "event_id": uuid.uuid4().hex, "at": datetime.now(UTC).isoformat(), **payload}
    props = pika.BasicProperties(
        content_type="application/json",
        delivery_mode=pika.DeliveryMode.Persistent,  # переживёт перезапуск RabbitMQ
        message_id=body["event_id"],
        type=event,
    )
    return json.dumps(body, ensure_ascii=False).encode(), props


class _Publisher:
    """Одно соединение на процесс API; при обрыве — переподключение."""

    def __init__(self) -> None:
        self._lock = threading.Lock()  # BlockingConnection не потокобезопасен
        self._connection: pika.BlockingConnection | None = None
        self._channel: BlockingChannel | None = None

    def _connect(self) -> BlockingChannel:
        params = pika.URLParameters(settings.rabbitmq_url)
        params.socket_timeout = 2
        params.blocked_connection_timeout = 2
        params.connection_attempts = 1
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        declare_topology(self._channel)
        return self._channel

    def publish(self, event: str, payload: dict) -> bool:
        body, props = make_message(event, payload)
        with self._lock:
            for attempt in (1, 2):  # вторая попытка — после переподключения
                try:
                    if self._channel is None or self._channel.is_closed:
                        self._connect()
                    self._channel.basic_publish(EXCHANGE, routing_key=event, body=body, properties=props)
                    return True
                except Exception as error:  # noqa: BLE001 — брокер не должен ронять API
                    self._channel = None
                    if attempt == 2:
                        log.warning("Событие %s не отправлено в RabbitMQ: %s", event, error)
        return False


_publisher = _Publisher()


def publish(event: str, payload: dict) -> bool:
    """Опубликовать событие. Возвращает False, если брокер выключен или недоступен."""
    if not settings.rabbitmq_url:
        return False  # брокер не настроен (например, в тестах) — просто пропускаем
    return _publisher.publish(event, payload)
