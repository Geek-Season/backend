"""Тихий лог ожидания RabbitMQ (app/worker/__main__.py)."""

import logging

from app.worker.__main__ import WaitReport


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_quiet_while_waiting(caplog):
    clock = Clock()
    report = WaitReport(clock)
    with caplog.at_level(logging.INFO, logger="worker"):
        for _ in range(27):  # 2 мин 15 с попыток раз в 5 с
            report.failed(ConnectionRefusedError(111, "Connection refused"))
            clock.now += 5
        report.connected()
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0].startswith("RabbitMQ недоступен")
    assert sum("ещё запускается" in line for line in lines) == 4  # 30, 60, 90, 120 с
    assert lines[-1] == "RabbitMQ доступен — ждал 2 мин 15 с"
    assert len(lines) == 6  # вместо ~27 × 12 строк


def test_connected_first_time_is_silent(caplog):
    with caplog.at_level(logging.INFO, logger="worker"):
        WaitReport(Clock()).connected()
    assert caplog.records == []


def test_report_restarts_after_reconnect(caplog):
    clock = Clock()
    report = WaitReport(clock)
    report.failed(OSError("x"))
    report.connected()
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="worker"):
        clock.now = 1000
        report.failed(OSError("снова"))
    assert caplog.records[0].getMessage().startswith("RabbitMQ недоступен (снова)")
