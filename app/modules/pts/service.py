"""PTS: начисление за ответ и полный пересчёт по истории попыток.

Каждая ПЕРВАЯ попытка ученика по задаче — «матч» ученика против задачи (Glicko-2):
верно — победа, неверно — поражение, у задач 19–21 — доля верных частей.
Одновременно меняются три рейтинга: общий PTS ученика, PTS ученика по номеру
и рейтинг самой задачи (задача «играет» против ученика с обратным результатом).

Поправки поверх Glicko-2 (см. план «PTS: новый рейтинг»):
- задача, у которой меньше CALIBRATING_TASK_GAMES ответов, ещё калибруется —
  ученик получает и теряет вдвое меньше;
- первые NEWBIE_GAMES ответов ученика потери вдвое меньше;
- PTS не опускается ниже 0.

Шаг 1: только пересчёт (python -m app.modules.pts) — на сайте PTS пока не виден.
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.modules.auth.models import User
from app.modules.progress.models import Attempt
from app.modules.pts.glicko import Rating, rate
from app.modules.pts.models import PtsChange, UserNumberRating
from app.modules.pts.ranks import Forecast, forecast
from app.modules.tasks.answers import answers_match
from app.modules.tasks.models import ExamNumber, Task

PLAYER_START = Rating(1000.0, 200.0, 0.06)  # до входного пробника (шаг 2) — у всех так
PLAYER_MIN_RD = 65.0  # ниже не опускаем: иначе рейтинг «застынет» (≈ K = 24 у Эло)
TASK_START_RD = 250.0
TASK_MIN_RD = 50.0
MAX_RD = 350.0
CALIBRATING_TASK_GAMES = 20
NEWBIE_GAMES = 20
TYPICAL_DEFAULT = 1200.0  # типичный рейтинг задач номера, в котором задач нет


def task_start_rating(difficulty: int) -> float:
    """Старт рейтинга задачи из ручной сложности: «Базовый» 30 → 1000, 80 → 1500."""
    return 700.0 + 10.0 * difficulty


def _clamp_rd(rd: float, low: float) -> float:
    return min(max(rd, low), MAX_RD)


# ---------- Один матч ----------

@dataclass
class Side:
    """Рейтинг одной стороны матча (изменяемый) + сколько матчей сыграно."""

    rating: float
    rd: float
    vol: float
    games: int = 0

    def as_rating(self) -> Rating:
        return Rating(self.rating, self.rd, self.vol)


@dataclass
class Result:
    pts_before: float
    pts_after: float
    number_before: float
    number_after: float
    task_before: float
    task_after: float


def _player_step(player: Side, task: Side, score: float, newbie_games: int) -> None:
    new = rate(player.as_rating(), [(task.as_rating(), score)])
    delta = new.rating - player.rating
    if task.games < CALIBRATING_TASK_GAMES:
        delta *= 0.5  # задача ещё калибруется — её рейтинг не точен
    if newbie_games < NEWBIE_GAMES and delta < 0:
        delta *= 0.5  # новичок не должен уйти с минусом в первый вечер
    player.rating = max(0.0, player.rating + delta)
    player.rd = _clamp_rd(new.rd, PLAYER_MIN_RD)
    player.vol = new.vol
    player.games += 1


def play(user: Side, number: Side, task: Side, score: float) -> Result:
    """Засчитать ответ: меняет user, number и task на месте, возвращает было/стало."""
    before = Result(user.rating, user.rating, number.rating, number.rating, task.rating, task.rating)
    task_snapshot = Side(task.rating, task.rd, task.vol, task.games)
    user_snapshot = user.as_rating()
    newbie_games = user.games

    _player_step(user, task_snapshot, score, newbie_games)
    _player_step(number, task_snapshot, score, newbie_games)

    new_task = rate(task_snapshot.as_rating(), [(user_snapshot, 1.0 - score)])
    task.rating, task.rd, task.vol = new_task.rating, _clamp_rd(new_task.rd, TASK_MIN_RD), new_task.vol
    task.games += 1

    before.pts_after, before.number_after, before.task_after = user.rating, number.rating, task.rating
    return before


# ---------- Результат попытки ----------

def score_of(attempt: Attempt, task: Task) -> float:
    """1 или 0; у 19–21 — доля верных частей (ответ сохранён как «19: 29; 20: 28 48; 21: —»)."""
    if not task.parts:
        return 1.0 if attempt.is_correct else 0.0
    given = {}
    for chunk in attempt.answer.split(";"):
        number, sep, answer = chunk.partition(":")
        if sep:
            given[number.strip()] = answer.strip()
    if not given:
        return 1.0 if attempt.is_correct else 0.0
    correct = sum(
        1 for part in task.parts
        if given.get(part.number, "—") not in ("", "—") and answers_match(given[part.number], part.correct_answer)
    )
    return correct / len(task.parts)


# ---------- Полный пересчёт ----------

@dataclass
class Summary:
    users: int = 0
    attempts: int = 0
    tasks_rated: int = 0
    changes: list[float] = field(default_factory=list)  # |Δ PTS| за ответ


def first_attempts(db: Session) -> list[Attempt]:
    """Первая попытка каждого ученика по каждой задаче, по времени."""
    first_ids = select(func.min(Attempt.id)).group_by(Attempt.user_id, Attempt.task_id)
    return list(db.scalars(
        select(Attempt).where(Attempt.id.in_(first_ids)).order_by(Attempt.created_at, Attempt.id)
    ))


def recompute_all(db: Session) -> Summary:
    """Пересчитать все PTS с нуля по первым попыткам. Можно запускать сколько угодно раз —
    результат одинаковый (порядок попыток фиксирован)."""
    tasks = {t.id: t for t in db.scalars(select(Task).options(selectinload(Task.parts)))}
    task_sides = {tid: Side(task_start_rating(t.difficulty), TASK_START_RD, 0.06) for tid, t in tasks.items()}
    user_sides: dict[int, Side] = {}
    number_sides: dict[tuple[int, int], Side] = {}
    changes: list[PtsChange] = []
    summary = Summary()

    for attempt in first_attempts(db):
        task = tasks.get(attempt.task_id)
        if task is None:
            continue
        user = user_sides.setdefault(attempt.user_id, Side(*_start()))
        number = number_sides.setdefault((attempt.user_id, task.exam_number_id), Side(*_start()))
        score = score_of(attempt, task)
        result = play(user, number, task_sides[task.id], score)
        summary.changes.append(abs(result.pts_after - result.pts_before))
        changes.append(PtsChange(
            attempt_id=attempt.id, user_id=attempt.user_id, task_id=task.id, exam_number_id=task.exam_number_id,
            score=score, pts_before=result.pts_before, pts_after=result.pts_after,
            number_pts_before=result.number_before, number_pts_after=result.number_after,
            task_before=result.task_before, task_after=result.task_after, created_at=attempt.created_at,
        ))

    # Записать: всё старое — стереть, новое — положить.
    db.execute(delete(PtsChange))
    db.execute(delete(UserNumberRating))
    for user in db.scalars(select(User)):
        side = user_sides.get(user.id, Side(*_start()))
        user.pts, user.pts_rd, user.pts_vol, user.pts_games = side.rating, side.rd, side.vol, side.games
    for tid, task in tasks.items():
        side = task_sides[tid]
        task.rating, task.rating_rd, task.rating_vol, task.rating_games = side.rating, side.rd, side.vol, side.games
    for (user_id, number_id), side in number_sides.items():
        db.add(UserNumberRating(user_id=user_id, exam_number_id=number_id, pts=side.rating, rd=side.rd,
                                vol=side.vol, games=side.games))
    db.add_all(changes)
    db.commit()

    summary.users = len(user_sides)
    summary.attempts = len(changes)
    summary.tasks_rated = sum(1 for s in task_sides.values() if s.games)
    return summary


def _start() -> tuple[float, float, float]:
    return PLAYER_START.rating, PLAYER_START.rd, PLAYER_START.vol


# ---------- Прогноз балла и ранг ----------

def typical_ratings(db: Session) -> dict[str, float]:
    """{код номера: медиана рейтингов активных задач номера} — «типичная задача» на экзамене."""
    by_code: dict[str, list[float]] = {n.code: [] for n in db.scalars(select(ExamNumber))}
    rows = db.execute(
        select(ExamNumber.code, Task.rating, Task.difficulty)
        .join(Task, Task.exam_number_id == ExamNumber.id)
        .where(Task.is_archived.is_(False))
    )
    for code, rating, difficulty in rows:
        by_code[code].append(rating if rating is not None else task_start_rating(difficulty))
    return {code: statistics.median(values) if values else TYPICAL_DEFAULT for code, values in by_code.items()}


def user_forecast(db: Session, user_id: int, typical: dict[str, float] | None = None) -> Forecast:
    typical = typical if typical is not None else typical_ratings(db)
    rows = db.execute(
        select(ExamNumber.code, UserNumberRating.pts, UserNumberRating.games)
        .join(UserNumberRating, UserNumberRating.exam_number_id == ExamNumber.id)
        .where(UserNumberRating.user_id == user_id)
    )
    return forecast({code: (pts, games) for code, pts, games in rows}, typical)


@dataclass
class ReportRow:
    user_id: int
    name: str
    games: int
    pts: float
    rd: float
    forecast: Forecast


def report(db: Session) -> list[ReportRow]:
    typical = typical_ratings(db)
    users = db.scalars(select(User).where(User.pts_games > 0).order_by(User.pts.desc()))
    return [
        ReportRow(u.id, u.display_name, u.pts_games, u.pts, u.pts_rd, user_forecast(db, u.id, typical))
        for u in users
    ]


def last_change_at(db: Session) -> datetime | None:
    return db.scalar(select(func.max(PtsChange.created_at)))
