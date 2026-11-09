"""Поправить порядок уже импортированных задач kompege: сверху (сначала новые) — самая новая задача kompege.

    python -m scripts.kompege.reorder --dry-run   # только показать, что поменяется
    python -m scripts.kompege.reorder             # поменять

Если к серверу нет консоли — то же самое делает админский эндпоинт
POST /tasks/reorder-kompege?dry_run=true|false (его можно вызвать из Swagger, /docs).

Логика — в app.modules.tasks.service.reorder_imported: внутри каждого номера отметки created_at
задач kompege переставляются по возрастанию taskId; ручные задачи, id, решения и PTS не меняются.
Скрипт пишет прямо в базу, поэтому в журнал админки он не попадает (эндпоинт — попадает).
"""

import argparse
import sys

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.modules.tasks.router import KOMPEGE_PREFIX
from app.modules.tasks.service import SameCreatedAt, reorder_imported


def reorder(db: Session, apply: bool) -> list[dict]:
    """Переставить (apply) или только посчитать. При SameCreatedAt ничего не меняется."""
    try:
        numbers = reorder_imported(db, KOMPEGE_PREFIX)
    except SameCreatedAt:
        db.rollback()
        raise
    if apply:
        db.commit()
    else:
        db.rollback()
    return numbers


def main() -> None:
    parser = argparse.ArgumentParser(description="Порядок задач kompege: сначала новые — сверху")
    parser.add_argument("--dry-run", action="store_true", help="ничего не менять, только показать")
    args = parser.parse_args()

    with SessionLocal() as db:
        try:
            numbers = reorder(db, apply=not args.dry_run)
        except SameCreatedAt as error:
            sys.exit(f"Ошибка: {error}")
    verb = "сменят место" if args.dry_run else "сменили место"
    for n in numbers:
        print(f"номер {n['number']}: задач kompege {n['total']}, {verb} {n['moved']}")
    moved = sum(n["moved"] for n in numbers)
    print(f"Итого {verb}: {moved}" + (" (пробный запуск — ничего не изменено)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
