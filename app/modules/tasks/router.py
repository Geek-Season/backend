"""HTTP-адреса банка заданий.

Здесь только «переводчик»: принять запрос → вызвать сервис → вернуть ответ
или правильный код ошибки. Вся логика — в service.py.

Страница «Задания» (сетка номеров):   GET  /numbers
Страница прототипов номера:           GET  /numbers/{code}/tasks
Одна задача:                          GET  /tasks/{id}
Проверка ответа:                      POST /tasks/{id}/check
Управление банком (только админ):     POST/PATCH/DELETE /tasks, GET /tasks/{id}/admin
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from redis import Redis
from sqlalchemy.orm import Session

from app.core import events
from app.core.db import get_db
from app.core.redis import get_redis
from app.modules.auth.deps import get_optional_user, require_admin
from app.modules.auth.models import User
from app.modules.progress import service as progress
from app.modules.rating import service as rating
from app.modules.tasks import service
from app.modules.tasks.models import Task
from app.modules.tasks.schemas import (
    AnswerResult,
    AnswerSubmission,
    ExamNumberRead,
    ProgressFilter,
    Sort,
    Source,
    TaskAdminRead,
    TaskCreate,
    TaskPage,
    TaskRead,
    TaskUpdate,
)

numbers_router = APIRouter(prefix="/numbers", tags=["Номера ЕГЭ"])
tasks_router = APIRouter(prefix="/tasks", tags=["Задачи"])


def _task_or_404(task_id: int, db: Session) -> Task:
    task = service.get_task(db, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задание не найдено")
    return task


def _unknown_number(code: str | None) -> HTTPException:
    return HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, f"Номер ЕГЭ «{code}» не существует"
    )


# ---------- Номера ----------

@numbers_router.get("", response_model=list[ExamNumberRead])
def get_numbers(db: Session = Depends(get_db), user: User | None = Depends(get_optional_user)):
    """Все номера ЕГЭ по порядку — карточки страницы «Задания».

    Для вошедшего ученика в поле solved — сколько задач номера он решил.
    """
    numbers = service.list_numbers(db)
    if user is not None:
        solved = progress.solved_by_number(db, user.id)
        for number in numbers:
            number["solved"] = solved.get(number["id"], 0)
    return numbers


@numbers_router.get("/{code}/tasks", response_model=TaskPage)
def get_number_tasks(
    code: str,
    source: Source | None = None,
    sort: Sort = "new",
    progress_filter: ProgressFilter | None = Query(
        None,
        alias="progress",
        description="Только для вошедших: solved — решённые, unsolved — ещё не решённые",
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """Прототипы номера. Ответы и разборы не отдаются."""
    number = service.get_number(db, code)
    if number is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Номер ЕГЭ не найден")

    only_ids = except_ids = None
    if progress_filter is not None:
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Войдите, чтобы фильтровать по прогрессу")
        solved_ids = progress.solved_task_ids(user.id)
        if progress_filter == "solved":
            only_ids = solved_ids
        else:
            except_ids = solved_ids

    total, tasks = service.list_number_tasks(
        db, number, source, sort, limit, offset, only_ids=only_ids, except_ids=except_ids
    )
    items = [TaskRead.model_validate(task) for task in tasks]
    solved = 0
    if user is not None:
        statuses = progress.task_statuses(db, user.id, [t.id for t in items])
        items = [t.model_copy(update={"status": statuses.get(t.id)}) for t in items]
        solved = progress.solved_by_number(db, user.id).get(number.id, 0)

    return {
        "number": {
            "code": number.code,
            "title": number.title,
            "tasks_count": service.count_number_tasks(db, number),
            "solved": solved,
        },
        "total": total,
        "items": items,
    }


# ---------- Задачи ----------

@tasks_router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)):
    return _task_or_404(task_id, db)


@tasks_router.post("/{task_id}/check", response_model=AnswerResult)
def check_task(
    task_id: int,
    submission: AnswerSubmission,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
    r: Redis = Depends(get_redis),
):
    """Проверить ответ. Код 200 значит «запрос обработан»; верность — в поле is_correct.

    Если ученик вошёл, ответ сохраняется в его статистику.
    """
    task = _task_or_404(task_id, db)
    result = service.check_answer(task, submission.answer)
    if user is not None:
        first_solve = progress.record_attempt(db, user.id, task.id, submission.answer, result["is_correct"])
        if first_solve:
            rating.add_point(r, user.id)
        # Событие для фонового воркера (достижения). Брокер недоступен — ответ всё равно уходит.
        events.publish("task.answered", {
            "user_id": user.id, "task_id": task.id,
            "is_correct": result["is_correct"], "first_solve": first_solve,
        })
    return result


@tasks_router.get("/{task_id}/admin", response_model=TaskAdminRead)
def get_task_admin(task_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Задача целиком — с ответом и разбором. Для редактора."""
    return _task_or_404(task_id, db)


@tasks_router.post("", response_model=TaskAdminRead, status_code=status.HTTP_201_CREATED)
def create_task(data: TaskCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    try:
        return service.create_task(db, data)
    except service.ExamNumberNotFound as error:
        raise _unknown_number(str(error)) from error


@tasks_router.patch("/{task_id}", response_model=TaskAdminRead)
def update_task(
    task_id: int, data: TaskUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    task = _task_or_404(task_id, db)
    try:
        return service.update_task(db, task, data)
    except service.ExamNumberNotFound as error:
        raise _unknown_number(str(error)) from error


@tasks_router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Задача переносится в архив и пропадает из списков."""
    task = _task_or_404(task_id, db)
    service.archive_task(db, task)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
