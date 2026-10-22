"""Фоновый воркер.  Запуск:  python -m app.worker

Подключается к RabbitMQ, создаёт очереди (если их нет) и слушает:
  achievements  — пересчёт достижений по событиям task.answered / exam.finished
  notifications — уведомления в Telegram по exam.finished / achievement.granted

Остановка — Ctrl+C. Необработанные сообщения останутся в очередях и
будут обработаны при следующем запуске.
"""

import functools
import logging
import time

import pika

from app.core.config import settings
from app.core.events import QUEUES, declare_topology
from app.worker.handlers import HANDLERS
from app.worker.process import process

log = logging.getLogger("worker")


def on_message(channel, method, properties, body, queue: str) -> None:
    result = process(channel, queue, method, properties, body, HANDLERS[queue])
    log.info("%-13s %-20s %s", queue, properties.type, result)


def run() -> None:
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = connection.channel()
    declare_topology(channel)
    channel.basic_qos(prefetch_count=10)  # не брать больше 10 сообщений за раз
    for queue in QUEUES:
        channel.basic_consume(queue, functools.partial(on_message, queue=queue))
    log.info("Воркер запущен, слушаю очереди: %s", ", ".join(QUEUES))
    channel.start_consuming()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("pika").setLevel(logging.WARNING)
    if not settings.rabbitmq_url:
        print("RABBITMQ_URL не задан — воркер не запущен.")
        return
    while True:  # брокер перезапустился — подключаемся заново
        try:
            run()
        except KeyboardInterrupt:
            break
        except pika.exceptions.AMQPConnectionError as error:
            log.warning("Нет связи с RabbitMQ (%s), повтор через 5 секунд", error)
            time.sleep(5)


if __name__ == "__main__":
    main()
