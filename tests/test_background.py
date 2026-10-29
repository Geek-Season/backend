"""Бот и воркер в одном контейнере: app/background.py."""

import os
import signal
import subprocess
import sys
import time

from app.background import run

PY = sys.executable


def cmd(code: str) -> list[str]:
    return [PY, "-c", code]


def test_crash_stops_others_and_fails(tmp_path):
    marker = tmp_path / "stopped"
    long_running = cmd(
        "import signal, sys, time, pathlib\n"
        f"signal.signal(signal.SIGTERM, lambda *a: (pathlib.Path({str(marker)!r}).write_text('ok'), sys.exit(0)))\n"
        "time.sleep(30)"
    )
    started = time.monotonic()
    code = run({"bot": cmd("import sys, time; time.sleep(0.3); sys.exit(3)"), "worker": long_running}, poll=0.05)
    assert code == 3  # контейнер завершится с ошибкой — Docker его перезапустит
    assert marker.read_text() == "ok"  # второй процесс получил SIGTERM
    assert time.monotonic() - started < 10


def test_clean_exit_keeps_others_running():
    # бот без BOT_TOKEN выходит с кодом 0 — воркер должен доработать
    code = run({"bot": cmd("pass"), "worker": cmd("import time; time.sleep(0.5)")}, poll=0.05)
    assert code == 0


def test_sigterm_stops_everything_cleanly(tmp_path):
    script = tmp_path / "runner.py"
    script.write_text(
        "import sys\n"
        "from app.background import run\n"
        f"sys.exit(run({{'a': [{PY!r}, '-c', 'import time; time.sleep(30)'], "
        f"'b': [{PY!r}, '-c', 'import time; time.sleep(30)']}}, poll=0.05))\n"
    )
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    proc = subprocess.Popen([PY, str(script)], env=env, stdout=subprocess.PIPE, text=True)
    time.sleep(1.5)
    proc.send_signal(signal.SIGTERM)
    out, _ = proc.communicate(timeout=15)
    assert proc.returncode == 0  # штатная остановка — не ошибка
    assert "SIGTERM" in out
