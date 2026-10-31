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


def run(on_connected=lambda: None) -> None:
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    on_connected()
    channel = connection.channel()
    declare_topology(channel)
    channel.basic_qos(prefetch_count=10)  # не брать больше 10 сообщений за раз
    for queue in QUEUES:
        channel.basic_consume(queue, functools.partial(on_message, queue=queue))
    log.info("Воркер запущен, слушаю очереди: %s", ", ".join(QUEUES))
    channel.start_consuming()


RETRY_SECONDS = 5      # как часто пробовать подключиться
REPORT_SECONDS = 30    # как часто напоминать в логе, что брокера всё ещё нет


def _duration(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    return f"{minutes} мин {sec} с" if minutes else f"{sec} с"


class WaitReport:
    """Тихий лог ожидания брокера.

    На хостинге RabbitMQ запускается до 2–3 минут. Раньше каждая попытка
    подключения (раз в 5 с) печатала ~12 строк ошибок pika с трейсбеком —
    сотни строк за старт. Теперь: одна строка в начале, напоминание раз в
    30 секунд и итог «подключился через …».
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.since = None
        self.last_report = None

    def failed(self, error: Exception) -> None:
        now = self.clock()
        if self.since is None:
            self.since = self.last_report = now
            reason = str(error) or type(error).__name__
            log.warning("RabbitMQ недоступен (%s) — жду, пробую каждые %d с", reason, RETRY_SECONDS)
        elif now - self.last_report >= REPORT_SECONDS:
            self.last_report = now
            log.info("RabbitMQ ещё запускается — жду %s", _duration(now - self.since))

    def connected(self) -> None:
        if self.since is not None:
            log.info("RabbitMQ доступен — ждал %s", _duration(self.clock() - self.since))
        self.since = self.last_report = None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # pika на каждую неудачную попытку пишет ERROR с трейсбеком; итог мы пишем сами.
    logging.getLogger("pika").setLevel(logging.CRITICAL)
    if not settings.rabbitmq_url:
        print("RABBITMQ_URL не задан — воркер не запущен.")
        return
    report = WaitReport()
    while True:  # брокер ещё запускается или перезапустился — подключаемся заново
        try:
            run(on_connected=report.connected)
        except KeyboardInterrupt:
            break
        except pika.exceptions.AMQPConnectionError as error:
            report.failed(error)
            time.sleep(RETRY_SECONDS)


if __name__ == "__main__":
    main()
