"""Логика банка заданий: что достать из базы и как проверить ответ.

Сервис ничего не знает про HTTP: он не возвращает коды 404/422,
а возвращает None или бросает своё исключение. Переводом в HTTP
занимается router.py. Так логику легко проверять тестами.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.modules.tasks.answers import answers_match
from app.modules.tasks.models import ExamNumber, Task
from app.modules.tasks.schemas import Sort, Source, TaskCreate, TaskUpdate


class ExamNumberNotFound(Exception):
    """Указан номер ЕГЭ, которого нет в базе."""


# ---------- Номера ----------

def _count_active_tasks():
    """Подзапрос: сколько неархивных задач в каждом номере."""
    return (
        select(Task.exam_number_id, func.count(Task.id).label("cnt"))
        .where(Task.is_archived.is_(False))
        .group_by(Task.exam_number_id)
        .subquery()
    )


def list_numbers(db: Session) -> list[dict]:
    counts = _count_active_tasks()
    rows = db.execute(
        select(ExamNumber, func.coalesce(counts.c.cnt, 0))
        .outerjoin(counts, counts.c.exam_number_id == ExamNumber.id)
        .order_by(ExamNumber.position)
    ).all()
    return [
        {"code": number.code, "title": number.title, "tasks_count": count}
        for number, count in rows
    ]


def get_number(db: Session, code: str) -> ExamNumber | None:
    return db.scalar(select(ExamNumber).where(ExamNumber.code == code))


def count_number_tasks(db: Session, number: ExamNumber) -> int:
    return db.scalar(
        select(func.count(Task.id)).where(
            Task.exam_number_id == number.id, Task.is_archived.is_(False)
        )
    )


# ---------- Задачи ----------

_ORDER = {
    "new": (Task.created_at.desc(), Task.id.desc()),
    "old": (Task.created_at.asc(), Task.id.asc()),
    "easy": (Task.difficulty.asc(), Task.id.asc()),
    "hard": (Task.difficulty.desc(), Task.id.asc()),
}


def list_number_tasks(
    db: Session,
    number: ExamNumber,
    source: Source | None,
    sort: Sort,
    limit: int,
    offset: int,
) -> tuple[int, list[Task]]:
    """Прототипы одного номера с фильтром, сортировкой и постраничным выводом."""
    query = select(Task).where(
        Task.exam_number_id == number.id,
        Task.is_archived.is_(False),
    )
    if source is not None:
        query = query.where(Task.source == source)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    tasks = db.scalars(
        query.options(joinedload(Task.exam_number))
        .order_by(*_ORDER[sort])
        .limit(limit)
        .offset(offset)
    ).all()
    return total, list(tasks)


def get_task(db: Session, task_id: int) -> Task | None:
    """Активная (не архивная) задача или None."""
    task = db.get(Task, task_id)
    if task is None or task.is_archived:
        return None
    return task


def create_task(db: Session, data: TaskCreate) -> Task:
    number = get_number(db, data.exam_number)
    if number is None:
        raise ExamNumberNotFound(data.exam_number)

    fields = data.model_dump(exclude={"exam_number"})
    # id не считаем сами: его выдаёт база, поэтому повторов не бывает.
    task = Task(**fields, exam_number=number)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(db: Session, task: Task, data: TaskUpdate) -> Task:
    changes = data.model_dump(exclude_unset=True)

    if "exam_number" in changes:
        code = changes.pop("exam_number")
        number = get_number(db, code) if code is not None else None
        if number is None:
            raise ExamNumberNotFound(code)
        task.exam_number = number

    for field, value in changes.items():
        setattr(task, field, value)

    db.commit()
    db.refresh(task)
    return task


def archive_task(db: Session, task: Task) -> None:
    """Вместо удаления прячем задачу: на неё могут ссылаться решения учеников."""
    task.is_archived = True
    db.commit()


def check_answer(task: Task, submitted_answer: str) -> dict:
    return {
        "task_id": task.id,
        "is_correct": answers_match(submitted_answer, task.correct_answer),
        "correct_answer": task.correct_answer,
        "explanation": task.explanation,
    }
