"""Поправить порядок уже импортированных задач kompege: сверху (сначала новые) — самая новая задача kompege.

    python -m scripts.kompege.reorder --dry-run   # только показать, что поменяется
    python -m scripts.kompege.reorder             # поменять

На сервере: docker compose exec api python -m scripts.kompege.reorder --dry-run

Ранние запуски импорта создавали задачи в порядке kompege — от новых к старым, и на сайте
«сначала новые» наверху оказывались старые. Сайт сортирует по tasks.created_at, поэтому внутри
каждого номера переставляем между задачами kompege их же отметки created_at: самой старой задаче
(меньший taskId) — самую раннюю. Задачи, добавленные вручную, не трогаем — их место в списке
сохраняется. Id задач, решения и PTS не меняются (created_at задачи больше нигде не используется).
Повторный запуск ничего не меняет. Всё — в одной транзакции.
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.modules.tasks.models import Task

PREFIX = "kompege:"


class SameTimeError(Exception):
    """У двух задач kompege одного номера одинаковый created_at — перестановка не задаст порядок."""


@dataclass
class NumberReport:
    code: str
    total: int
    moved: int


def kompege_id(task: Task) -> int:
    return int(task.external_id.removeprefix(PREFIX))


def reorder(db: Session, apply: bool) -> list[NumberReport]:
    """Переставить created_at задач kompege по возрастанию taskId. Без apply — только посчитать."""
    tasks = db.scalars(select(Task).where(Task.external_id.like(f"{PREFIX}%"))).all()
    groups: dict[str, list[Task]] = defaultdict(list)
    for task in tasks:
        groups[task.exam_number.code].append(task)

    reports = []
    for code, group in sorted(groups.items(), key=lambda item: item[1][0].exam_number.position):
        times = sorted(task.created_at for task in group)
        same = sorted({t for t, n in zip(times, times[1:]) if t == n})
        if same:
            db.rollback()  # предыдущие номера могли уже поменяться в сессии — откатываем всё
            raise SameTimeError(f"номер {code}: у нескольких задач одинаковое время создания {same[0]} — "
                                "порядок перестановкой не задать, ничего не изменено")
        moved = 0
        for task, time in zip(sorted(group, key=kompege_id), times):
            if task.created_at != time:
                moved += 1
                task.created_at = time
        reports.append(NumberReport(code, len(group), moved))

    if apply:
        db.commit()
    else:
        db.rollback()
    return reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Порядок задач kompege: сначала новые — сверху")
    parser.add_argument("--dry-run", action="store_true", help="ничего не менять, только показать")
    args = parser.parse_args()

    with SessionLocal() as db:
        try:
            reports = reorder(db, apply=not args.dry_run)
        except SameTimeError as error:
            sys.exit(f"Ошибка: {error}")
    verb = "сменят место" if args.dry_run else "сменили место"
    for r in reports:
        print(f"номер {r.code}: задач kompege {r.total}, {verb} {r.moved}")
    moved = sum(r.moved for r in reports)
    print(f"Итого {verb}: {moved}" + (" (пробный запуск — ничего не изменено)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
