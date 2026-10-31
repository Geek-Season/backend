"""Пересчитать PTS по всем попыткам и напечатать отчёт (шаг 1 — сайт PTS пока не показывает).

Запуск:  python -m app.modules.pts
При старте контейнера api запускается сам (Dockerfile), отчёт — в логе api.
Ошибка пересчёта не мешает запуску сайта: печатается и только.
"""

import statistics
import sys
import traceback

from sqlalchemy import select

from app.core.db import SessionLocal
from app.modules.pts import service
from app.modules.tasks.models import Task

TOP = 20


def main() -> None:
    with SessionLocal() as db:
        summary = service.recompute_all(db)
        rows = service.report(db)
        median = statistics.median(summary.changes) if summary.changes else 0
        print(f"PTS (пробный пересчёт, на сайте пока не виден): учеников {summary.users}, "
              f"засчитано первых попыток {summary.attempts}, задач с ответами {summary.tasks_rated}, "
              f"медиана изменения за ответ {median:.0f} PTS")
        for place, row in enumerate(rows[:TOP], start=1):
            f = row.forecast
            print(f"PTS {place:>2}. {row.name[:20]:<20} PTS {row.pts:>5.0f} ±{row.rd:<3.0f} ответов {row.games:>3} "
                  f"прогноз {f.primary_exact:4.1f} перв. → {f.test_score:>3} баллов — {f.rank.title}")
        rated = list(db.scalars(select(Task).where(Task.rating_games >= 5).order_by(Task.rating.desc())))
        if rated:
            for label, task in (("самая сложная", rated[0]), ("самая лёгкая", rated[-1])):
                print(f"PTS {label} задача (≥5 ответов): id {task.id}, № {task.exam_number.code}, "
                      f"рейтинг {task.rating:.0f} (сложность в редакторе {task.difficulty})")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — пересчёт не должен ронять запуск сайта
        print("PTS: пересчёт не удался (сайт работает как обычно):", file=sys.stderr)
        traceback.print_exc()
