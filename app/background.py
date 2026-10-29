"""Фоновые процессы в одном контейнере: Telegram-бот и воркер очередей.

Запуск:  python -m app.background

Зачем вместе. Хостинг (Netrun) не запускал отдельный сервис с ботом, а контейнер
воркера работал. Поэтому бот и воркер живут в одном контейнере — как два
отдельных процесса, каждый со своей ошибкой и своими логами.

Правила:
- процесс упал (код не 0) — останавливаем второй и выходим с ошибкой: Docker
  перезапустит контейнер целиком (restart: on-failure);
- процесс завершился спокойно (код 0) — например, бот без BOT_TOKEN или воркер
  без RABBITMQ_URL — второй продолжает работать;
- Docker останавливает контейнер (SIGTERM) — передаём сигнал обоим и ждём их.
"""

import signal
import subprocess
import sys
import time

PROCESSES = {
    "bot": [sys.executable, "-m", "app.bot"],
    "worker": [sys.executable, "-m", "app.worker"],
}
POLL_SECONDS = 1.0
STOP_TIMEOUT = 10


def log(message: str) -> None:
    print(f"[background] {message}", flush=True)


def run(processes: dict[str, list[str]] = PROCESSES, poll: float = POLL_SECONDS) -> int:
    """Запустить процессы и следить за ними. Возвращает код выхода для контейнера."""
    running = {name: subprocess.Popen(cmd) for name, cmd in processes.items()}
    log("запущены: " + ", ".join(running))
    state = {"stopping": False}

    def stop_all() -> None:
        for proc in running.values():
            if proc.poll() is None:
                proc.terminate()
        deadline = time.monotonic() + STOP_TIMEOUT
        for proc in running.values():
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                proc.kill()

    def on_signal(signum, _frame) -> None:
        state["stopping"] = True
        log(f"получен сигнал {signal.Signals(signum).name} — останавливаю процессы")
        stop_all()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    exit_code = 0
    while running:
        for name, proc in list(running.items()):
            code = proc.poll()
            if code is None:
                continue
            del running[name]
            if state["stopping"]:
                continue
            if code == 0:
                log(f"{name} завершился (код 0), остальные работают дальше")
            else:
                log(f"{name} упал с кодом {code} — останавливаю остальные, Docker перезапустит контейнер")
                exit_code = code if code > 0 else 1
                state["stopping"] = True
                stop_all()
        time.sleep(poll)
    return exit_code


if __name__ == "__main__":
    sys.exit(run())
