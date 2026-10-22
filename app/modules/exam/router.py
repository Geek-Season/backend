"""HTTP-адреса пробника. Всё — только для вошедших.

POST /exam/start                      — начать (или продолжить идущий)
GET  /exam/current                    — идущий пробник (null, если нет)
GET  /exam/history                    — завершённые пробники
GET  /exam/{id}                       — пробник; ответы и разбор — после завершения
PUT  /exam/{id}/answers/{позиция}     — сохранить ответ (автосохранение)
POST /exam/{id}/finish                — завершить и получить результат
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field
from redis import Redis
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.redis import get_redis
from app.modules.auth.deps import get_current_user
from app.modules.auth.models import User
from app.modules.exam import service
from app.modules.exam.models import ExamSession

router = APIRouter(prefix="/exam", tags=["Пробник"])


# ---------- Схемы ----------

class ExamItem(BaseModel):
    position: int
    exam_number: str
    title: str
    points: int
    task_id: int
    statement: str
    image_url: str | None
    answer: str | None
    # Только после завершения:
    is_correct: bool | None = None
    correct_answer: str | None = None
    explanation: str | None = None


class ExamView(BaseModel):
    id: int
    started_at: datetime
    deadline_at: datetime
    finished_at: datetime | None
    server_now: datetime = Field(description="Время сервера — по нему сайт сверяет свой отсчёт")
    duration_minutes: int
    max_score: int
    primary_score: int | None
    test_score: int | None = Field(description="Тестовый балл по шкале Рособрнадзора 2026 (после завершения)")
    test_score_approximate: bool | None = Field(
        description="true — в варианте меньше 29 первичных баллов (в банке не все номера), балл приблизительный"
    )
    passed: bool | None = Field(description="Порог Рособрнадзора: 40 тестовых")
    university_passed: bool | None = Field(description="Порог для поступления в вуз: 46 тестовых")
    answered: int
    items: list[ExamItem]


class ExamHistoryItem(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime
    primary_score: int
    max_score: int
    test_score: int
    test_score_approximate: bool
    tasks_count: int


class ExamAnswerIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    answer: str = Field(max_length=100, description="Пустая строка — стереть ответ", examples=["22"])


# ---------- Вспомогательное ----------

def _own_or_404(session_id: int, db: Session, r: Redis, user: User) -> ExamSession:
    session = service.get_for_user(db, r, session_id, user.id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пробник не найден")
    return session


# ---------- Адреса ----------

@router.post("/start", response_model=ExamView)
def start_exam(
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    r: Redis = Depends(get_redis),
):
    """201 — создан новый пробник; 200 — уже идёт, возвращаем его же."""
    try:
        session, created = service.start(db, r, user.id)
    except service.NoTasks:
        raise HTTPException(status.HTTP_409_CONFLICT, "В банке нет задач для пробника") from None
    except service.AlreadyStarting:
        raise HTTPException(status.HTTP_409_CONFLICT, "Пробник уже создаётся — обновите страницу") from None
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return service.to_view(session)


@router.get("/current", response_model=ExamView | None)
def current_exam(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    r: Redis = Depends(get_redis),
):
    """Идущий пробник или null — «пробника нет» это нормальное состояние, а не ошибка."""
    session = service.get_active(db, r, user.id)
    return service.to_view(session) if session else None


@router.get("/history", response_model=list[ExamHistoryItem])
def exam_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [
        {
            "id": s.id,
            "started_at": s.started_at,
            "finished_at": s.finished_at,
            "primary_score": s.primary_score,
            "max_score": s.max_score,
            "test_score": service.test_score_fields(s)["test_score"],
            "test_score_approximate": service.test_score_fields(s)["test_score_approximate"],
            "tasks_count": len(s.answers),
        }
        for s in service.history(db, user.id)
    ]


@router.get("/{session_id}", response_model=ExamView)
def get_exam(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    r: Redis = Depends(get_redis),
):
    return service.to_view(_own_or_404(session_id, db, r, user))


@router.put("/{session_id}/answers/{position}", response_model=ExamItem)
def save_answer(
    session_id: int,
    position: int,
    data: ExamAnswerIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    r: Redis = Depends(get_redis),
):
    session = _own_or_404(session_id, db, r, user)
    try:
        service.save_answer(db, r, session, position, data.answer)
    except service.ExamFinished:
        raise HTTPException(status.HTTP_409_CONFLICT, "Пробник уже завершён") from None
    except service.TimeIsUp:
        raise HTTPException(status.HTTP_409_CONFLICT, "Время вышло — пробник завершён") from None
    except service.NoSuchPosition:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "В варианте нет такой задачи") from None
    view = service.to_view(session)
    return next(item for item in view["items"] if item["position"] == position)


@router.post("/{session_id}/finish", response_model=ExamView)
def finish_exam(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    r: Redis = Depends(get_redis),
):
    session = _own_or_404(session_id, db, r, user)
    return service.to_view(service.finish(db, r, session))
