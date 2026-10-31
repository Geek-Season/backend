"""Логика пробника.

Правила:
- у ученика может быть только один незавершённый пробник: «Начать» второй раз
  возвращает тот же (поэтому перезагрузка страницы ничего не ломает);
- время считает сервер: deadline_at в базе; после него ответы не принимаются,
  а пробник завершается сам при первом же обращении к нему;
- до завершения правильные ответы и разборы не отдаются;
- «Завершить» повторно — вернёт тот же результат, без пересчёта;
- баллы: номера 26 и 27 — по 2 первичных балла, остальные — по 1 (как в ЕГЭ);
- задача 19–21 занимает в варианте три позиции (19, 20, 21) — по одной на вопрос,
  поэтому полный вариант — 27 позиций и 29 первичных баллов.

Redis здесь:
- exam:active:{user_id} → id сессии, TTL = время до конца пробника. Быстрый ответ
  на «есть ли у ученика идущий пробник» без запроса в базу; ключ исчезает сам,
  когда время вышло.
- exam:start_lock:{user_id} на 10 секунд — защита от двойного нажатия «Начать»:
  два одновременных запроса не создадут два пробника.
"""

from datetime import UTC, datetime, timedelta

from redis import Redis
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core import events
from app.core.config import settings
from app.modules.exam.models import ExamAnswer, ExamSession
from app.modules.exam.scale import FULL_PRIMARY_MAX, MIN_PASSING, MIN_UNIVERSITY, to_test_score
from app.modules.progress import service as progress
from app.modules.rating import service as rating
from app.modules.tasks.answers import answers_match
from app.modules.tasks.models import ExamNumber, Task, TaskPart

TWO_POINT_NUMBERS = {"26", "27"}


class NoTasks(Exception):
    """В банке нет ни одной задачи — вариант не собрать."""


class AlreadyStarting(Exception):
    """Пробник уже создаётся параллельным запросом."""


class ExamFinished(Exception):
    """Пробник уже завершён — ответы не принимаются."""


class TimeIsUp(Exception):
    """Время вышло — ответ не принят, пробник завершён."""


class NoSuchPosition(Exception):
    """В варианте нет задачи с таким номером позиции."""


def _now() -> datetime:
    return datetime.now(UTC)


def _active_key(user_id: int) -> str:
    return f"exam:active:{user_id}"


def _load(db: Session, session_id: int) -> ExamSession | None:
    return db.scalar(
        select(ExamSession)
        .where(ExamSession.id == session_id)
        .options(
            selectinload(ExamSession.answers).selectinload(ExamAnswer.task).selectinload(Task.exam_number),
            selectinload(ExamSession.answers).selectinload(ExamAnswer.task).selectinload(Task.files),
            selectinload(ExamSession.answers).selectinload(ExamAnswer.part),
        )
    )


def is_time_up(session: ExamSession, now: datetime | None = None) -> bool:
    return (now or _now()) >= session.deadline_at


# ---------- Начать ----------

def get_active(db: Session, r: Redis, user_id: int) -> ExamSession | None:
    """Идущий пробник ученика или None. Просроченный — завершает."""
    cached = r.get(_active_key(user_id))
    if cached is not None:
        session = _load(db, int(cached))
    else:  # Redis пуст или ключ истёк — проверяем базу
        session_id = db.scalar(
            select(ExamSession.id).where(
                ExamSession.user_id == user_id, ExamSession.finished_at.is_(None)
            )
        )
        session = _load(db, session_id) if session_id else None

    if session is None or session.finished_at is not None:
        return None
    if is_time_up(session):
        finish(db, r, session)
        return None
    return session


def start(db: Session, r: Redis, user_id: int) -> tuple[ExamSession, bool]:
    """Начать пробник. Возвращает (сессия, создана_ли_новая)."""
    active = get_active(db, r, user_id)
    if active is not None:
        return active, False

    lock = f"exam:start_lock:{user_id}"
    if not r.set(lock, 1, nx=True, ex=10):
        raise AlreadyStarting
    try:
        # По одной случайной задаче на каждый номер (номера — по порядку).
        numbered = (
            select(
                Task.id,
                ExamNumber.code,
                ExamNumber.position,
                func.row_number()
                .over(partition_by=Task.exam_number_id, order_by=func.random())
                .label("rn"),
            )
            .join(ExamNumber, ExamNumber.id == Task.exam_number_id)
            .where(Task.is_archived.is_(False))
            .subquery()
        )
        picked = db.execute(
            select(numbered.c.id, numbered.c.code)
            .where(numbered.c.rn == 1)
            .order_by(numbered.c.position)
        ).all()
        if not picked:
            raise NoTasks

        now = _now()
        duration = timedelta(minutes=settings.exam_duration_minutes)
        parts = {}
        for part in db.scalars(
            select(TaskPart).where(TaskPart.task_id.in_([task_id for task_id, _ in picked])).order_by(TaskPart.number)
        ):
            parts.setdefault(part.task_id, []).append(part)

        answers = []
        for task_id, code in picked:
            # Задача из частей — позиция на каждую часть; обычная — одна позиция.
            for part in parts.get(task_id, [None]):
                answers.append(ExamAnswer(
                    position=len(answers) + 1,
                    task_id=task_id,
                    part_id=part.id if part else None,
                    points=2 if code in TWO_POINT_NUMBERS else 1,
                ))
        session = ExamSession(
            user_id=user_id,
            started_at=now,
            deadline_at=now + duration,
            max_score=sum(a.points for a in answers),
            answers=answers,
        )
        db.add(session)
        db.commit()
        r.set(_active_key(user_id), session.id, ex=int(duration.total_seconds()))
        return _load(db, session.id), True
    finally:
        r.delete(lock)


# ---------- Отвечать ----------

def save_answer(db: Session, r: Redis, session: ExamSession, position: int, answer: str) -> ExamAnswer:
    """Сохранить ответ на позицию варианта. Пустая строка — стереть ответ."""
    if session.finished_at is not None:
        # Завершён по времени (finished_at = deadline) — честно говорим «время вышло».
        raise TimeIsUp if session.finished_at >= session.deadline_at else ExamFinished
    if is_time_up(session):
        finish(db, r, session)
        raise TimeIsUp

    item = next((a for a in session.answers if a.position == position), None)
    if item is None:
        raise NoSuchPosition
    item.answer = answer or None
    item.updated_at = _now()
    db.commit()
    return item


# ---------- Завершить ----------

def finish(db: Session, r: Redis, session: ExamSession) -> ExamSession:
    """Проверить ответы и подвести итог. Повторный вызов ничего не меняет."""
    if session.finished_at is not None:
        return session

    now = _now()
    score = 0
    new_points = 0
    by_task: dict[int, list[ExamAnswer]] = {}
    for item in session.answers:
        item.is_correct = bool(item.answer) and answers_match(item.answer, item.correct_answer)
        if item.is_correct:
            score += item.points
        by_task.setdefault(item.task_id, []).append(item)

    # Ответы пробника идут в общую статистику и слабые темы: одна попытка на задачу.
    # Задача 19–21 засчитывается решённой, если верны все три части.
    for task_id, items in by_task.items():
        if not any(i.answer for i in items):
            continue
        if items[0].part is None:
            text = items[0].answer
        else:
            text = "; ".join(f"{i.part.number}: {i.answer or '—'}" for i in items)[:100]
        if progress.record_attempt(
            db, session.user_id, task_id, text, all(i.is_correct for i in items), commit=False
        ):
            new_points += 1

    session.primary_score = score
    session.finished_at = min(now, session.deadline_at)  # опоздание не «продлевает» экзамен
    db.commit()

    r.delete(_active_key(session.user_id))
    rating.add_point(r, session.user_id, new_points)
    # Событие для воркера: уведомление в Telegram и достижения. Публикуется и при
    # автозавершении по времени — ученик узнает результат, даже закрыв вкладку.
    scores = test_score_fields(session)
    events.publish("exam.finished", {
        "user_id": session.user_id,
        "session_id": session.id,
        "primary_score": score,
        "max_score": session.max_score,
        "test_score": scores["test_score"],
        "test_score_approximate": scores["test_score_approximate"],
    })
    return session


# ---------- Чтение ----------

def get_for_user(db: Session, r: Redis, session_id: int, user_id: int) -> ExamSession | None:
    """Пробник по id, только если он принадлежит ученику. Просроченный — завершает."""
    session = _load(db, session_id)
    if session is None or session.user_id != user_id:
        return None
    if session.finished_at is None and is_time_up(session):
        finish(db, r, session)
    return session


def history(db: Session, user_id: int) -> list[ExamSession]:
    return list(db.scalars(
        select(ExamSession)
        .where(ExamSession.user_id == user_id, ExamSession.finished_at.is_not(None))
        .options(selectinload(ExamSession.answers))
        .order_by(ExamSession.started_at.desc())
        .limit(20)
    ))


def test_score_fields(session: ExamSession) -> dict:
    """Тестовый балл по официальной шкале.

    Шкала рассчитана на полный вариант (29 первичных). Если в банке есть не все
    номера, вариант меньше — тестовый балл помечается как приблизительный.
    """
    if session.primary_score is None:
        return {"test_score": None, "test_score_approximate": None, "passed": None, "university_passed": None}
    test = to_test_score(session.primary_score)
    return {
        "test_score": test,
        "test_score_approximate": session.max_score < FULL_PRIMARY_MAX,
        "passed": test >= MIN_PASSING,
        "university_passed": test >= MIN_UNIVERSITY,
    }


def to_view(session: ExamSession) -> dict:
    """Пробник для API. Ответы и разборы — только после завершения."""
    finished = session.finished_at is not None
    items = []
    for a in session.answers:
        item = {
            "position": a.position,
            # У части 19–21 — её собственный номер: «19», «20» или «21».
            "exam_number": a.part.number if a.part else a.task.exam_number.code,
            "title": a.task.exam_number.title,
            "points": a.points,
            "task_id": a.task_id,
            "statement": a.task.statement,
            "question": a.part.question if a.part else None,
            "image_url": a.task.image_url,
            "images": a.task.student_images,
            "attachments": a.task.attachments,
            "answer": a.answer,
        }
        if finished:
            item |= {
                "is_correct": a.is_correct,
                "correct_answer": a.correct_answer,
                "explanation": (a.part.explanation or a.task.explanation) if a.part else a.task.explanation,
            }
        items.append(item)

    def utc(moment: datetime | None) -> datetime | None:
        # Время всегда в UTC — как бы его ни вернула база.
        return moment.astimezone(UTC) if moment else None

    return {
        "id": session.id,
        "started_at": utc(session.started_at),
        "deadline_at": utc(session.deadline_at),
        "finished_at": utc(session.finished_at),
        "server_now": _now(),
        "duration_minutes": round((session.deadline_at - session.started_at).total_seconds() / 60),
        "max_score": session.max_score,
        "primary_score": session.primary_score,
        **test_score_fields(session),
        "answered": sum(1 for a in session.answers if a.answer),
        "items": items,
    }
