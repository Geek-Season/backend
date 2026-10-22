"""Логика банка заданий: что достать из базы и как проверить ответ.

Сервис ничего не знает про HTTP: он не возвращает коды 404/422,
а возвращает None или бросает своё исключение. Переводом в HTTP
занимается router.py. Так логику легко проверять тестами.
"""

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.modules.tasks.answers import answers_match
from app.modules.tasks.models import ExamNumber, Task, TaskPart
from app.modules.tasks.schemas import PartIn, Sort, Source, TaskCreate, TaskUpdate

# Номера, задачи которых состоят из частей: одно условие — несколько вопросов.
PART_NUMBERS = {"19-21": ("19", "20", "21")}


class ExamNumberNotFound(Exception):
    """Указан номер ЕГЭ, которого нет в базе."""


class InvalidTask(Exception):
    """Задача собрана неправильно: нет ответа, лишние или недостающие части."""


class WrongAnswerFormat(Exception):
    """Для задачи с частями прислали answer, для обычной — answers."""


def summary(parts) -> str:
    """Сводка ответов частей для колонки tasks.correct_answer: «19: 29; 20: 28 48; 21: 44»."""
    return "; ".join(f"{p.number}: {p.correct_answer}" for p in parts)[:100]


def _check_shape(code: str, correct_answer: str | None, parts: list[PartIn] | None) -> None:
    expected = PART_NUMBERS.get(code)
    if expected is None:
        if parts:
            raise InvalidTask(f"У номера {code} не бывает частей — части есть только у 19–21")
        if not correct_answer:
            raise InvalidTask("Укажите правильный ответ")
        return
    numbers = sorted(p.number for p in parts or [])
    if numbers != sorted(expected):
        raise InvalidTask(f"Задача {code} должна состоять из частей {', '.join(expected)}")


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
        {"id": number.id, "code": number.code, "title": number.title, "tasks_count": count}
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
    only_ids: Select | None = None,
    except_ids: Select | None = None,
) -> tuple[int, list[Task]]:
    """Прототипы одного номера с фильтром, сортировкой и постраничным выводом.

    only_ids / except_ids — подзапросы со списком id задач: оставить только их
    или, наоборот, убрать. Так модуль прогресса передаёт «решённые задачи ученика»,
    а банк заданий не знает, как они считаются.
    """
    query = select(Task).where(
        Task.exam_number_id == number.id,
        Task.is_archived.is_(False),
    )
    if source is not None:
        query = query.where(Task.source == source)
    if only_ids is not None:
        query = query.where(Task.id.in_(only_ids))
    if except_ids is not None:
        query = query.where(Task.id.not_in(except_ids))

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    tasks = db.scalars(
        query.options(joinedload(Task.exam_number), selectinload(Task.parts))
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

    _check_shape(number.code, data.correct_answer, data.parts)

    fields = data.model_dump(exclude={"exam_number", "parts"})
    # id не считаем сами: его выдаёт база, поэтому повторов не бывает.
    task = Task(**fields, exam_number=number)
    if data.parts:
        task.parts = [TaskPart(**p.model_dump()) for p in data.parts]
        task.correct_answer = summary(task.parts)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def update_task(db: Session, task: Task, data: TaskUpdate) -> Task:
    changes = data.model_dump(exclude_unset=True, exclude={"parts"})

    number = task.exam_number
    if "exam_number" in changes:
        code = changes.pop("exam_number")
        number = get_number(db, code) if code is not None else None
        if number is None:
            raise ExamNumberNotFound(code)

    # На части ссылаются ответы пробников, поэтому игру нельзя превратить в обычную задачу и наоборот.
    if (number.code in PART_NUMBERS) != (task.exam_number.code in PART_NUMBERS):
        raise InvalidTask("Задачу 19–21 нельзя перенести в другой номер (и наоборот) — создайте новую")

    # Какой задача станет после изменений — и проверяем её целиком.
    parts = data.parts if "parts" in data.model_fields_set else (
        [PartIn.model_validate(p, from_attributes=True) for p in task.parts] or None
    )
    answer = changes.get("correct_answer", task.correct_answer if not task.parts else None)
    _check_shape(number.code, answer, parts)

    task.exam_number = number
    for field, value in changes.items():
        setattr(task, field, value)
    if "parts" in data.model_fields_set:
        # Меняем части на месте (по номеру 19/20/21): их id остаются прежними.
        existing = {p.number: p for p in task.parts}
        for new in data.parts or []:
            part = existing.get(new.number)
            if part is None:
                task.parts.append(TaskPart(**new.model_dump()))
            else:
                for field, value in new.model_dump().items():
                    setattr(part, field, value)
    if task.parts:
        task.correct_answer = summary(task.parts)

    db.commit()
    db.refresh(task)
    return task


def archive_task(db: Session, task: Task) -> None:
    """Вместо удаления прячем задачу: на неё могут ссылаться решения учеников."""
    task.is_archived = True
    db.commit()


def check_answer(task: Task, answer: str | None, answers: dict[str, str] | None = None) -> dict:
    """Проверить ответ. У задачи с частями каждая часть проверяется отдельно;
    задача решена, если верны все части."""
    if bool(task.parts) != (answers is not None):
        raise WrongAnswerFormat
    if not task.parts:
        return {
            "task_id": task.id,
            "is_correct": answers_match(answer, task.correct_answer),
            "correct_answer": task.correct_answer,
            "explanation": task.explanation,
            "parts": [],
        }
    parts = []
    for part in task.parts:
        given = answers.get(part.number, "")
        parts.append({
            "number": part.number,
            "answer": given,
            "is_correct": bool(given) and answers_match(given, part.correct_answer),
            "correct_answer": part.correct_answer,
            "explanation": part.explanation,
        })
    return {
        "task_id": task.id,
        "is_correct": all(p["is_correct"] for p in parts),
        "correct_answer": task.correct_answer,
        "explanation": task.explanation,
        "parts": parts,
    }


def answer_text(result: dict, answer: str | None) -> str:
    """Что сохранить в статистику как ответ ученика."""
    if not result["parts"]:
        return answer
    return "; ".join(f"{p['number']}: {p['answer'] or '—'}" for p in result["parts"])[:100]
