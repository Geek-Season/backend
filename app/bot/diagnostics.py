"""Диагностика при запуске бота: сеть до Telegram и память контейнера.

Нужна, чтобы по логам хостинга было видно, ГДЕ застревает бот:
не находится адрес, не открывается соединение, Telegram не отвечает
или процессу не хватает памяти.
"""

import resource
import socket
import time
from pathlib import Path

TELEGRAM_HOST = "api.telegram.org"


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _mb(value: str | None) -> str:
    if value is None:
        return "?"
    if value == "max":
        return "без ограничения"
    try:
        number = int(value)
    except ValueError:
        return value
    if number >= 2**60:  # cgroup v1 пишет огромное число вместо «без ограничения»
        return "без ограничения"
    return f"{number / 1024 / 1024:.0f} МБ"


def memory_report() -> str:
    """Сколько памяти занял процесс и какой лимит у контейнера (cgroup v2 или v1)."""
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: ru_maxrss в КБ
    limit = _read("/sys/fs/cgroup/memory.max") or _read("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    current = _read("/sys/fs/cgroup/memory.current") or _read("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    return f"память: процесс {rss_mb:.0f} МБ, контейнер занято {_mb(current)} из {_mb(limit)}"


def network_report(host: str = TELEGRAM_HOST, port: int = 443, timeout: float = 5.0) -> list[str]:
    """Проверить по шагам: DNS → TCP-соединение. Возвращает строки для лога."""
    lines = []
    started = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as error:
        return [f"DNS {host}: ошибка — {error}"]
    addresses = sorted({info[4][0] for info in infos})
    lines.append(f"DNS {host}: {', '.join(addresses)} ({(time.monotonic() - started) * 1000:.0f} мс)")

    for family, kind, proto, _, address in infos[:2]:
        started = time.monotonic()
        try:
            with socket.socket(family, kind, proto) as sock:
                sock.settimeout(timeout)
                sock.connect(address)
            lines.append(f"TCP {address[0]}:{port}: соединение есть ({(time.monotonic() - started) * 1000:.0f} мс)")
        except OSError as error:
            lines.append(f"TCP {address[0]}:{port}: НЕТ соединения за {timeout:.0f} с — {error or type(error).__name__}")
    return lines
