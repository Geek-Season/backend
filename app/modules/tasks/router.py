"""HTTP-адреса банка заданий.

Здесь только «переводчик»: принять запрос → вызвать сервис → вернуть ответ
или правильный код ошибки. Вся логика — в service.py.

Страница «Задания» (сетка номеров):   GET  /numbers
Страница прототипов номера:           GET  /numbers/{code}/tasks
Одна задача:                          GET  /tasks/{id}
Проверка ответа:                      POST /tasks/{id}/check
Ответ и разбор (после 3 ошибок):      GET  /tasks/{id}/solution
Управление банком (только админ):     POST/PATCH/DELETE /tasks, GET /tasks/{id}/admin
Картинки и файлы к задаче (админ):    POST /tasks/{id}/files, DELETE /tasks/{id}/files/{file_id}
Скачать картинку или файл:            GET  /files/{имя}
"""

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import events
from app.core.db import get_db
from app.core.redis import get_redis
from app.modules.auth.deps import get_optional_user, require_admin
from app.modules.auth.models import ROLE_ADMIN, User
from app.modules.progress import service as progress
from app.modules.progress.models import Attempt
from app.modules.pts import service as pts
from app.modules.pts.schemas import rank_read
from app.modules.tasks import files, reveal, service
from app.modules.tasks.models import FILE_IMAGE, Task
from app.modules.tasks.schemas import (
    AnswerResult,
    AnswerSubmission,
    ExamNumberRead,
    ProgressFilter,
    Sort,
    Source,
    TaskAdminRead,
    TaskCreate,
    TaskFileRead,
    TaskPage,
    TaskRead,
    TaskSolution,
    TaskUpdate,
)

numbers_router = APIRouter(prefix="/numbers", tags=["Номера ЕГЭ"])
tasks_router = APIRouter(prefix="/tasks", tags=["Задачи"])
files_router = APIRouter(prefix="/files", tags=["Файлы задач"])


def _client(request: Request) -> str:
    """Адрес ученика (за прокси хостинга — из X-Forwarded-For, см. Dockerfile)."""
    return request.client.host if request.client else "unknown"


def _with_solution_flags(
    items: list[TaskRead], db: Session, user: User | None, r: Redis, request: Request
) -> list[TaskRead]:
    """Отметить задачи, у которых ученику уже можно открыть ответ и разбор."""
    ids = [t.id for t in items]
    opened = reveal.user_open(db, user.id, ids) if user is not None else reveal.guest_open(r, _client(request), ids)
    return [t.model_copy(update={"solution_available": True}) if t.id in opened else t for t in items]


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
    request: Request = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
    r: Redis = Depends(get_redis),
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
    items = _with_solution_flags(items, db, user, r, request)

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
def get_task(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
    r: Redis = Depends(get_redis),
):
    task = TaskRead.model_validate(_task_or_404(task_id, db))
    return _with_solution_flags([task], db, user, r, request)[0]


@tasks_router.post("/{task_id}/check", response_model=AnswerResult)
def check_task(
    task_id: int,
    submission: AnswerSubmission,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
    r: Redis = Depends(get_redis),
):
    """Проверить ответ. Код 200 значит «запрос обработан»; верность — в поле is_correct.

    Если ученик вошёл, ответ сохраняется в его статистику.
    При неверном ответе правильный ответ и разбор не отдаются, пока неверных ответов
    не наберётся reveal_after_wrong (3); тогда solution_available = true.
    """
    task = _task_or_404(task_id, db)
    try:
        result = service.check_answer(task, submission.answer, submission.answers)
    except service.WrongAnswerFormat:
        detail = "У этой задачи три части: пришлите answers" if task.parts else "Пришлите answer"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail) from None
    if user is not None:
        first_solve = progress.record_attempt(
            db, user.id, task.id, service.answer_text(result, submission.answer), result["is_correct"]
        )
        solved, wrong = reveal.user_state(db, user.id, task.id)
        attempt = db.scalar(
            select(Attempt).where(Attempt.user_id == user.id, Attempt.task_id == task.id).order_by(Attempt.id.desc())
        )
        outcome = pts.record_answer(db, user, task, attempt)
        pts_info = {
            "pts_delta": round(outcome.delta, 1) if outcome.counted else None,
            "pts_repeat": not outcome.counted,
            "rank_up": rank_read(outcome.rank_up),
        }
        # Событие для фонового воркера (достижения, в том числе за ранг — поэтому после PTS).
        # Брокер недоступен — ответ всё равно уходит.
        events.publish("task.answered", {
            "user_id": user.id, "task_id": task.id,
            "is_correct": result["is_correct"], "first_solve": first_solve,
        })
    else:
        solved, wrong = reveal.guest_record(r, _client(request), task.id, result["is_correct"])
        pts_info = {}

    if not result["is_correct"]:
        result = service.hide_solution(result)
    return {
        **result,
        **pts_info,
        "solution_available": reveal.is_open(solved, wrong),
        "attempts_left": reveal.attempts_left(solved, wrong),
    }


@tasks_router.get("/{task_id}/solution", response_model=TaskSolution)
def get_solution(
    task_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
    r: Redis = Depends(get_redis),
):
    """Правильный ответ и разбор — когда задача решена или неверных ответов набралось
    reveal_after_wrong (3). Иначе 403 с числом оставшихся попыток.

    Посмотреть ответ — не нарушение: решить задачу после этого можно как обычно.
    """
    task = _task_or_404(task_id, db)
    if user is not None and user.role == ROLE_ADMIN:
        return service.solution(task)
    if user is not None:
        solved, wrong = reveal.user_state(db, user.id, task.id)
    else:
        solved, wrong = reveal.guest_state(r, _client(request), task.id)
    if not reveal.is_open(solved, wrong):
        left = reveal.attempts_left(solved, wrong)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Разбор откроется после ещё {left} неверн{'ого ответа' if left == 1 else 'ых ответов'}",
        )
    return service.solution(task)


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
    except service.InvalidTask as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


@tasks_router.patch("/{task_id}", response_model=TaskAdminRead)
def update_task(
    task_id: int, data: TaskUpdate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    task = _task_or_404(task_id, db)
    try:
        return service.update_task(db, task, data)
    except service.ExamNumberNotFound as error:
        db.rollback()
        raise _unknown_number(str(error)) from error
    except service.InvalidTask as error:
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


@tasks_router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    """Задача переносится в архив и пропадает из списков."""
    task = _task_or_404(task_id, db)
    service.archive_task(db, task)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- Картинки и файлы к задаче (только админ) ----------

@tasks_router.post("/{task_id}/files", response_model=TaskFileRead, status_code=status.HTTP_201_CREATED)
def upload_task_file(
    task_id: int,
    file: UploadFile = File(description="Картинка (PNG, JPEG, GIF, WebP) или файл к заданию"),
    kind: Literal["image", "attachment"] = Form(
        description="image — показать в условии; attachment — файл для скачивания"
    ),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    """Загрузить картинку или файл к задаче. Форма multipart/form-data: поля file и kind."""
    task = _task_or_404(task_id, db)
    try:
        return files.save_file(db, task, file, kind)
    except files.FileRejected as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


@tasks_router.delete("/{task_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task_file(
    task_id: int, file_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    """Удалить картинку или файл задачи (и из базы, и с диска)."""
    _task_or_404(task_id, db)
    record = files.get_task_file(db, task_id, file_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    files.delete_file(db, record)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@files_router.get("/{stored_name}", response_class=FileResponse)
def download_file(stored_name: str, db: Session = Depends(get_db)):
    """Отдать картинку или файл задачи.

    Картинки открываются в браузере, файлы к заданию скачиваются с исходным именем.
    Имя на диске случайное и не меняется, поэтому браузер может хранить файл в кэше.
    """
    found = files.find_stored(db, stored_name)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден")
    record, path = found
    is_image = record.kind == FILE_IMAGE
    return FileResponse(
        path,
        media_type=record.content_type,
        headers={
            "Content-Disposition": files.content_disposition(
                "inline" if is_image else "attachment", record.original_name
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
