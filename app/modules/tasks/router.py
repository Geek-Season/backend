"""HTTP-адреса банка заданий.

Здесь только «переводчик»: принять запрос → вызвать сервис → вернуть ответ
или правильный код ошибки. Вся логика — в service.py.

Страница «Задания» (сетка номеров):   GET  /numbers
Страница прототипов номера:           GET  /numbers/{code}/tasks
Одна задача:                          GET  /tasks/{id}
Проверка ответа:                      POST /tasks/{id}/check
Управление банком (для автора задач): POST/PATCH/DELETE /tasks
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.tasks import service
from app.modules.tasks.models import Task
from app.modules.tasks.schemas import (
    AnswerResult,
    AnswerSubmission,
    ExamNumberRead,
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
def get_numbers(db: Session = Depends(get_db)):
    """Все номера ЕГЭ по порядку — карточки страницы «Задания»."""
    return service.list_numbers(db)


@numbers_router.get("/{code}/tasks", response_model=TaskPage)
def get_number_tasks(
    code: str,
    source: Source | None = None,
    sort: Sort = "new",
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Прототипы номера. Ответы и разборы не отдаются."""
    number = service.get_number(db, code)
    if number is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Номер ЕГЭ не найден")

    total, tasks = service.list_number_tasks(db, number, source, sort, limit, offset)
    return {
        "number": {
            "code": number.code,
            "title": number.title,
            "tasks_count": service.count_number_tasks(db, number),
        },
        "total": total,
        "items": tasks,
    }


# ---------- Задачи ----------

@tasks_router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)):
    return _task_or_404(task_id, db)


@tasks_router.post("/{task_id}/check", response_model=AnswerResult)
def check_task(task_id: int, submission: AnswerSubmission, db: Session = Depends(get_db)):
    """Проверить ответ. Код 200 значит «запрос обработан»; верность — в поле is_correct."""
    task = _task_or_404(task_id, db)
    return service.check_answer(task, submission.answer)


@tasks_router.post("", response_model=TaskAdminRead, status_code=status.HTTP_201_CREATED)
def create_task(data: TaskCreate, db: Session = Depends(get_db)):
    try:
        return service.create_task(db, data)
    except service.ExamNumberNotFound as error:
        raise _unknown_number(str(error)) from error


@tasks_router.patch("/{task_id}", response_model=TaskAdminRead)
def update_task(task_id: int, data: TaskUpdate, db: Session = Depends(get_db)):
    task = _task_or_404(task_id, db)
    try:
        return service.update_task(db, task, data)
    except service.ExamNumberNotFound as error:
        raise _unknown_number(str(error)) from error


@tasks_router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    """Задача переносится в архив и пропадает из списков."""
    task = _task_or_404(task_id, db)
    service.archive_task(db, task)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
