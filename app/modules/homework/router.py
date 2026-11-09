"""HTTP-адреса домашек.

Ученик (нужно войти; черновики и чужие домашки — 404):
GET  /homework                                — мои домашки
GET  /homework/{id}                           — задачи с моими ответами (разборы — после сдачи)
POST /homework/{id}/tasks/{task_id}/answer    — единственная попытка
POST /homework/{id}/submit                    — сдать (пропуски — неверно в счёте домашки)

Админ:
GET    /admin/homework                        — все домашки, в том числе черновики
POST   /admin/homework                        — создать черновик
GET    /admin/homework/tasks                  — банк задач для конструктора: ?number=&q=&user_ids=
POST   /admin/homework/random                 — случайные задачи номера, которых ученики ещё не решали
GET    /admin/homework/{id}                   — домашка для конструктора
PATCH  /admin/homework/{id}                   — черновик — всё; отправленная — только title, comment
POST   /admin/homework/{id}/send              — отправить
POST   /admin/homework/{id}/students          — добавить учеников
DELETE /admin/homework/{id}                   — удалить (попытки и PTS остаются)
GET    /admin/homework/{id}/results           — таблица «ученик × задача»
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.modules.auth.deps import get_current_user, require_admin
from app.modules.auth.models import User
from app.modules.homework import service
from app.modules.homework.schemas import (
    HomeworkAdminDetail,
    HomeworkAnswerResult,
    HomeworkCard,
    HomeworkDetail,
    HomeworkIn,
    HomeworkPatch,
    HomeworkResults,
    HomeworkRow,
    PickerPage,
    PickerTask,
    RandomIn,
    StudentsIn,
)
from app.modules.tasks import service as tasks
from app.modules.tasks.schemas import AnswerSubmission

router = APIRouter(prefix="/homework", tags=["Домашки"])
admin_router = APIRouter(prefix="/admin/homework", tags=["Админка: домашки"])

NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "Домашка не найдена")


def _http(error: Exception) -> HTTPException:
    if isinstance(error, service.NotFound):
        return NOT_FOUND
    if isinstance(error, service.Conflict):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


# ---------- Ученик ----------

@router.get("", response_model=list[HomeworkCard])
def my_homework(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Несданные — сверху."""
    return service.my_list(db, user)


@router.get("/{homework_id}", response_model=HomeworkDetail)
def get_homework(homework_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return service.my_detail(db, user, homework_id)
    except service.NotFound:
        raise NOT_FOUND from None


@router.post("/{homework_id}/tasks/{task_id}/answer", response_model=HomeworkAnswerResult)
def answer_task(
    homework_id: int,
    task_id: int,
    submission: AnswerSubmission,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Одна попытка на задачу: второй ответ — 409. Ответ идёт в статистику и PTS как обычный.

    Правильный ответ и разбор не отдаются — они откроются после сдачи.
    """
    try:
        return service.answer(db, user, homework_id, task_id, submission.answer, submission.answers)
    except tasks.WrongAnswerFormat:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Неверный формат ответа: answer или answers (для 19–21)"
        ) from None
    except (service.NotFound, service.Conflict) as error:
        raise _http(error) from None


@router.post("/{homework_id}/submit", response_model=HomeworkDetail)
def submit_homework(homework_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return service.submit(db, user, homework_id)
    except (service.NotFound, service.Conflict) as error:
        raise _http(error) from None


# ---------- Админ (сначала адреса без {id}) ----------

@admin_router.get("", response_model=list[HomeworkRow])
def admin_list(db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    return service.admin_list(db)


@admin_router.post("", response_model=HomeworkAdminDetail, status_code=status.HTTP_201_CREATED)
def create_homework(body: HomeworkIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        return service.create(db, admin, body.title, body.comment, body.task_ids, body.user_ids)
    except service.Invalid as error:
        db.rollback()
        raise _http(error) from None


@admin_router.get("/tasks", response_model=PickerPage)
def pick_tasks(
    number: str | None = Query(None, max_length=10),
    q: str = Query("", max_length=100),
    user_ids: list[int] = Query([], description="Ученики домашки: отметить задачи, которые они уже решали"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return service.picker(db, number, q, user_ids, limit, offset)


@admin_router.post("/random", response_model=list[PickerTask])
def random_tasks(body: RandomIn, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    try:
        return service.random_tasks(db, body.number, body.count, body.user_ids, body.exclude_task_ids)
    except service.Invalid as error:
        raise _http(error) from None


@admin_router.get("/{homework_id}", response_model=HomeworkAdminDetail)
def admin_detail(homework_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    try:
        return service.admin_detail(db, homework_id)
    except service.NotFound:
        raise NOT_FOUND from None


@admin_router.patch("/{homework_id}", response_model=HomeworkAdminDetail)
def update_homework(
    homework_id: int, body: HomeworkPatch, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    try:
        return service.update(db, homework_id, body.model_dump(exclude_unset=True))
    except (service.NotFound, service.Conflict, service.Invalid) as error:
        db.rollback()
        raise _http(error) from None


@admin_router.post("/{homework_id}/send", response_model=HomeworkAdminDetail)
def send_homework(homework_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    try:
        return service.send(db, homework_id)
    except (service.NotFound, service.Conflict, service.Invalid) as error:
        raise _http(error) from None


@admin_router.post("/{homework_id}/students", response_model=HomeworkAdminDetail)
def add_students(
    homework_id: int, body: StudentsIn, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    try:
        return service.add_students(db, homework_id, body.user_ids)
    except (service.NotFound, service.Invalid) as error:
        db.rollback()
        raise _http(error) from None


@admin_router.delete("/{homework_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_homework(homework_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    try:
        service.remove(db, homework_id)
    except service.NotFound:
        raise NOT_FOUND from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.get("/{homework_id}/results", response_model=HomeworkResults)
def homework_results(homework_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    try:
        return service.results(db, homework_id)
    except service.NotFound:
        raise NOT_FOUND from None
