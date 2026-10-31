"""PTS: начисление за ответ, за пробник и полный пересчёт по истории.

Каждая ПЕРВАЯ встреча ученика с задачей — «матч» ученика против задачи (Glicko-2):
верно — победа, неверно — поражение, у 19–21 — доля верных частей. Одновременно
меняются три рейтинга: общий PTS ученика, PTS ученика по номеру и рейтинг задачи
(задача «играет» против ученика с обратным результатом).

Первая встреча — это первая попытка на странице номера ИЛИ задача в пробнике
(пустой ответ в пробнике = ошибка). Повторные попытки PTS не меняют.

Поправки поверх Glicko-2 (план «PTS: новый рейтинг»):
- задача, у которой меньше CALIBRATING_TASK_GAMES ответов, ещё калибруется —
  ученик получает и теряет вдвое меньше;
- первые NEWBIE_GAMES ответов ученика потери вдвое меньше;
- PTS не опускается ниже 0.

Ранг (ranks.py) — по прогнозу балла ЕГЭ. Он появляется после первого
завершённого пробника («входной пробник»), как калибровка в Доте: PTS ученика
сдвигается так, чтобы прогноз совпал с баллом за пробник, и первый ранг = этот балл.
Дальше PTS и ранг меняются ответами; ранг понижается с запасом в 1 балл.

Живое начисление (record, finish_exam) и пересчёт с нуля (recompute_all) идут через
один и тот же Engine — результат пересчёта совпадает с тем, что ученик видел.
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.modules.admin.models import AdminAction
from app.modules.auth.models import User
from app.modules.exam.models import ExamSession
from app.modules.progress.models import Attempt
from app.modules.pts import ranks
from app.modules.pts.glicko import Rating, expected, rate
from app.modules.pts.models import PtsChange, UserNumberRating
from app.modules.tasks.answers import answers_match
from app.modules.tasks.models import ExamNumber, Task

PLAYER_START = Rating(1000.0, 200.0, 0.06)
NUMBER_START_RD = 200.0  # PTS по номеру стартует с общего PTS ученика в этот момент
PLAYER_MIN_RD = 65.0  # ниже не опускаем: иначе рейтинг «застынет» (≈ K = 24 у Эло)
TASK_START_RD = 250.0
TASK_MIN_RD = 50.0
MAX_RD = 350.0
CALIBRATING_TASK_GAMES = 20
NEWBIE_GAMES = 20
TYPICAL_DEFAULT = 1200.0  # типичный рейтинг задач номера, в котором задач нет
EXAM_ATTEMPT_WINDOW = timedelta(minutes=5)  # попытка из пробника записывается при его завершении
# Действия админа, которые меняют PTS: записаны в журнал и повторяются при пересчёте.
ACTION_SET_PTS = "pts.set"
ACTION_SET_RANK = "pts.rank"
ACTION_RECALIBRATE = "pts.recalibrate"
ACTION_RESET_STATS = "stats.reset"
PTS_ACTIONS = (ACTION_SET_PTS, ACTION_SET_RANK, ACTION_RECALIBRATE, ACTION_RESET_STATS)

ANCHOR_MIN, ANCHOR_MAX = 0.0, 3000.0  # где ищем PTS при калибровке по входному пробнику


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

    @property
    def delta(self) -> float:
        return self.pts_after - self.pts_before


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
    result = Result(user.rating, user.rating, number.rating, number.rating, task.rating, task.rating)
    task_snapshot = Side(task.rating, task.rd, task.vol, task.games)
    user_snapshot = user.as_rating()
    newbie_games = user.games

    _player_step(user, task_snapshot, score, newbie_games)
    _player_step(number, task_snapshot, score, newbie_games)

    new_task = rate(task_snapshot.as_rating(), [(user_snapshot, 1.0 - score)])
    task.rating, task.rd, task.vol = new_task.rating, _clamp_rd(new_task.rd, TASK_MIN_RD), new_task.vol
    task.games += 1

    result.pts_after, result.number_after, result.task_after = user.rating, number.rating, task.rating
    return result


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


def exam_primary_29(session: ExamSession) -> float:
    """Балл за пробник, приведённый к полным 29 первичным (в банке может не быть всех номеров)."""
    if not session.max_score:
        return 0.0
    return (session.primary_score or 0) * ranks.FULL_PRIMARY / session.max_score


def exam_scores(session: ExamSession) -> dict[int, float]:
    """{task_id: результат} по завершённому пробнику: доля верных пунктов задачи (пустой — неверно)."""
    by_task: dict[int, list[bool]] = {}
    for item in session.answers:
        by_task.setdefault(item.task_id, []).append(bool(item.is_correct))
    return {task_id: sum(oks) / len(oks) for task_id, oks in by_task.items()}


# ---------- Движок: общий для живого начисления и пересчёта ----------

@dataclass
class Outcome:
    """Что произошло с учеником после ответа или пробника."""

    delta: float = 0.0  # изменение общего PTS
    counted: int = 0  # сколько задач засчитано (0 — повторная попытка)
    rank_up: ranks.Rank | None = None  # новый ранг выше прежнего (или первый ранг)
    calibrated_now: bool = False  # этот пробник — входной: ранг появился


@dataclass
class UserState:
    side: Side
    rank_key: str | None = None
    calibrated_at: datetime | None = None


@dataclass
class Engine:
    task_numbers: dict[int, int]  # task_id → exam_number_id
    number_codes: dict[int, str]  # exam_number_id → код («5», «19-21»)
    tasks: dict[int, Side]
    users: dict[int, UserState] = field(default_factory=dict)
    numbers: dict[tuple[int, int], Side] = field(default_factory=dict)
    seen: set[tuple[int, int]] = field(default_factory=set)  # (ученик, задача), уже встречались
    archived: set[int] = field(default_factory=set)  # не участвуют в «типичной задаче» номера

    def typical(self) -> dict[str, float]:
        """{код номера: медиана рейтингов его задач} — «типичная задача» номера на экзамене."""
        ratings: dict[int, list[float]] = {nid: [] for nid in self.number_codes}
        for task_id, side in self.tasks.items():
            if task_id not in self.archived:
                ratings[self.task_numbers[task_id]].append(side.rating)
        return {
            self.number_codes[nid]: statistics.median(values) if values else TYPICAL_DEFAULT
            for nid, values in ratings.items()
        }

    def forecast(self, user_id: int) -> ranks.Forecast:
        user = self.users[user_id]
        number_pts = {
            self.number_codes[nid]: side.rating for (uid, nid), side in self.numbers.items() if uid == user_id
        }
        return ranks.forecast(number_pts, user.side.rating, self.typical())

    def play(self, user_id: int, task_id: int, score: float) -> Result | None:
        """Матч ученика и задачи. None — уже встречались (повторная попытка)."""
        if (user_id, task_id) in self.seen or task_id not in self.tasks:
            return None
        self.seen.add((user_id, task_id))
        user = self.users.setdefault(user_id, UserState(Side(*_player_start())))
        nid = self.task_numbers[task_id]
        number = self.numbers.setdefault((user_id, nid), Side(user.side.rating, NUMBER_START_RD, PLAYER_START.vol))
        return play(user.side, number, self.tasks[task_id], score)

    def anchor(self, user_id: int, target_primary: float) -> None:
        """Калибровка по входному пробнику: сдвинуть общий PTS и PTS по всем номерам на одно и то же
        число так, чтобы прогноз стал равен баллу за пробник. Разница между номерами сохраняется."""
        user = self.users[user_id]
        overall = user.side.rating
        mine = {nid: side for (uid, nid), side in self.numbers.items() if uid == user_id}
        tilt = {self.number_codes[nid]: side.rating - overall for nid, side in mine.items()}
        typical = self.typical()

        def primary_at(pts: float) -> float:
            return sum(ranks.POINTS.get(code, 1) * expected(pts + tilt.get(code, 0.0), rating)
                       for code, rating in typical.items())

        low, high = ANCHOR_MIN, ANCHOR_MAX
        for _ in range(50):  # прогноз растёт с PTS — делим отрезок пополам
            middle = (low + high) / 2
            if primary_at(middle) < target_primary:
                low = middle
            else:
                high = middle
        shift = (low + high) / 2 - overall
        user.side.rating = max(0.0, overall + shift)
        for side in mine.values():
            side.rating = max(0.0, side.rating + shift)

    # ---------- Правки администратора (те же и вживую, и при пересчёте) ----------

    def state(self, user_id: int) -> UserState:
        return self.users.setdefault(user_id, UserState(Side(*_player_start())))

    def shift(self, user_id: int, delta: float) -> None:
        """Сдвинуть общий PTS и PTS по всем номерам на одно число (не ниже нуля)."""
        user = self.state(user_id)
        user.side.rating = max(0.0, user.side.rating + delta)
        for (uid, _), side in self.numbers.items():
            if uid == user_id:
                side.rating = max(0.0, side.rating + delta)

    def set_pts(self, user_id: int, pts: float, at: datetime) -> None:
        """Админ выставил PTS: ранг — строго по прогнозу; ученик считается откалиброванным,
        иначе следующий пробник «перекалибровал» бы его и стёр правку."""
        user = self.state(user_id)
        self.shift(user_id, max(0.0, pts) - user.side.rating)
        user.calibrated_at = user.calibrated_at or at
        user.rank_key = self.forecast(user_id).rank.key

    def set_rank(self, user_id: int, rank_key: str, at: datetime) -> None:
        """Админ выбрал ранг: PTS подбирается так, чтобы прогноз оказался в середине ранга."""
        rank = ranks.by_key(rank_key)
        upper = ranks.next_up(rank)
        top = upper.min_primary if upper else ranks.FULL_PRIMARY
        user = self.state(user_id)
        self.anchor(user_id, (rank.min_primary + top) / 2)
        user.calibrated_at = user.calibrated_at or at
        user.rank_key = rank.key

    def recalibrate(self, user_id: int) -> None:
        """Сбросить калибровку: ранга нет, следующий завершённый пробник — снова входной."""
        user = self.state(user_id)
        user.calibrated_at = None
        user.rank_key = None

    def forget(self, user_id: int) -> None:
        """Сброс всей статистики: ученик как новый."""
        self.users.pop(user_id, None)
        for key in [k for k in self.numbers if k[0] == user_id]:
            del self.numbers[key]
        self.seen = {pair for pair in self.seen if pair[0] != user_id}

    def apply_admin(self, user_id: int, action: str, details: dict, at: datetime) -> None:
        if action == ACTION_SET_PTS:
            self.set_pts(user_id, float(details["pts"]), at)
        elif action == ACTION_SET_RANK:
            self.set_rank(user_id, details["rank"], at)
        elif action == ACTION_RECALIBRATE:
            self.recalibrate(user_id)
        elif action == ACTION_RESET_STATS:
            self.forget(user_id)

    def update_rank(
        self, user_id: int, calibrate_at: datetime | None = None, calibrate_primary: float | None = None
    ) -> tuple[ranks.Rank | None, bool]:
        """Пересчитать ранг. calibrate_* — завершён пробник (балл за него, приведённый к 29).
        Возвращает (новый ранг, если он выше прежнего; стал ли ученик откалиброван сейчас)."""
        user = self.users[user_id]
        calibrated_now = False
        if user.calibrated_at is None:
            if calibrate_at is None:
                return None, False  # до входного пробника ранга нет
            user.calibrated_at = calibrate_at
            calibrated_now = True
            if calibrate_primary is not None:
                self.anchor(user_id, calibrate_primary)
        old = ranks.by_key(user.rank_key)
        new = ranks.next_rank(old, self.forecast(user_id).primary)
        user.rank_key = new.key
        promoted = old is None or ranks.RANKS.index(new) < ranks.RANKS.index(old)
        return (new if promoted else None), calibrated_now


def _player_start() -> tuple[float, float, float]:
    return PLAYER_START.rating, PLAYER_START.rd, PLAYER_START.vol


def _side_of_task(task: Task) -> Side:
    if task.rating is None:
        return Side(task_start_rating(task.difficulty), TASK_START_RD, 0.06, 0)
    return Side(task.rating, task.rating_rd, task.rating_vol, task.rating_games)


def _numbers(db: Session) -> dict[int, str]:
    return dict(db.execute(select(ExamNumber.id, ExamNumber.code)).all())


# ---------- Живое начисление ----------

def _load_engine(db: Session, user: User) -> Engine:
    """Движок с состоянием одного ученика (строка ученика заблокирована до конца транзакции)."""
    db.execute(select(User.id).where(User.id == user.id).with_for_update())
    db.refresh(user)
    active = list(db.scalars(select(Task).where(Task.is_archived.is_(False))))
    engine = Engine(
        task_numbers={t.id: t.exam_number_id for t in active},
        number_codes=_numbers(db),
        tasks={t.id: _side_of_task(t) for t in active},
    )
    engine.users[user.id] = UserState(
        Side(user.pts, user.pts_rd, user.pts_vol, user.pts_games), user.rank_key, user.calibrated_at
    )
    for row in db.scalars(select(UserNumberRating).where(UserNumberRating.user_id == user.id)):
        engine.numbers[(user.id, row.exam_number_id)] = Side(row.pts, row.rd, row.vol, row.games)
    engine.seen = {(user.id, tid) for tid in db.scalars(select(PtsChange.task_id).where(PtsChange.user_id == user.id))}
    return engine


def _save(db: Session, engine: Engine, user: User, touched: list[tuple[int, Result, float, int | None, int | None, datetime]]) -> None:
    state = engine.users[user.id]
    user.pts, user.pts_rd, user.pts_vol, user.pts_games = state.side.rating, state.side.rd, state.side.vol, state.side.games
    user.rank_key, user.calibrated_at = state.rank_key, state.calibrated_at
    for task_id, result, score, attempt_id, session_id, at in touched:
        task = db.get(Task, task_id)
        side = engine.tasks[task_id]
        task.rating, task.rating_rd, task.rating_vol, task.rating_games = side.rating, side.rd, side.vol, side.games
        nid = engine.task_numbers[task_id]
        number = engine.numbers[(user.id, nid)]
        row = db.get(UserNumberRating, (user.id, nid))
        if row is None:
            row = UserNumberRating(user_id=user.id, exam_number_id=nid)
            db.add(row)
        row.pts, row.rd, row.vol, row.games = number.rating, number.rd, number.vol, number.games
        db.add(PtsChange(
            attempt_id=attempt_id, exam_session_id=session_id, user_id=user.id, task_id=task_id,
            exam_number_id=nid, score=score, pts_before=result.pts_before, pts_after=result.pts_after,
            number_pts_before=result.number_before, number_pts_after=result.number_after,
            task_before=result.task_before, task_after=result.task_after, created_at=at,
        ))


def record_answer(db: Session, user: User, task: Task, attempt: Attempt) -> Outcome:
    """Ответ на странице номера (попытка уже сохранена). Повторная попытка — Outcome(counted=0)."""
    engine = _load_engine(db, user)
    score = score_of(attempt, task)
    result = engine.play(user.id, task.id, score)
    if result is None:
        db.commit()  # снять блокировку
        return Outcome()
    rank_up, _ = engine.update_rank(user.id)
    _save(db, engine, user, [(task.id, result, score, attempt.id, None, attempt.created_at)])
    db.commit()
    return Outcome(delta=result.delta, counted=1, rank_up=rank_up)


def finish_exam(db: Session, user: User, session: ExamSession) -> Outcome:
    """Завершённый пробник: каждая задача, которую ученик встречает впервые, — матч.

    Первый завершённый пробник — входной: после него у ученика появляется ранг.
    """
    engine = _load_engine(db, user)
    touched = []
    before = engine.users[user.id].side.rating
    for task_id, score in exam_scores(session).items():
        result = engine.play(user.id, task_id, score)
        if result is not None:
            touched.append((task_id, result, score, None, session.id, session.finished_at))
    rank_up, calibrated_now = engine.update_rank(
        user.id, calibrate_at=session.finished_at, calibrate_primary=exam_primary_29(session)
    )
    _save(db, engine, user, touched)
    db.commit()
    return Outcome(engine.users[user.id].side.rating - before, len(touched), rank_up, calibrated_now)


def admin_change(db: Session, user: User, action: str, details: dict, at: datetime) -> None:
    """Правка администратора вживую (без commit — его делает вызывающий вместе с журналом)."""
    engine = _load_engine(db, user)
    engine.apply_admin(user.id, action, details, at)
    state = engine.state(user.id)
    user.pts, user.pts_rd, user.pts_vol, user.pts_games = state.side.rating, state.side.rd, state.side.vol, state.side.games
    user.rank_key, user.calibrated_at = state.rank_key, state.calibrated_at
    for (uid, nid), side in engine.numbers.items():
        row = db.get(UserNumberRating, (uid, nid))
        if row is not None:
            row.pts, row.rd, row.vol, row.games = side.rating, side.rd, side.vol, side.games


# ---------- Полный пересчёт ----------

@dataclass
class Summary:
    users: int = 0
    counted: int = 0
    tasks_rated: int = 0
    changes: list[float] = field(default_factory=list)  # |Δ PTS| за ответ


@dataclass(order=True)
class _Event:
    at: datetime
    order: int  # при равном времени: сначала попытки, потом пробник целиком, потом правка админа
    key: int
    user_id: int = field(compare=False)
    attempt: Attempt | None = field(compare=False, default=None)
    session: ExamSession | None = field(compare=False, default=None)
    admin: AdminAction | None = field(compare=False, default=None)


def recompute_all(db: Session) -> Summary:
    """Пересчитать всё с нуля: попытки и пробники по времени. Повторный запуск даёт тот же результат."""
    tasks = {t.id: t for t in db.scalars(select(Task).options(selectinload(Task.parts)))}
    engine = Engine(
        task_numbers={tid: t.exam_number_id for tid, t in tasks.items()},
        number_codes=_numbers(db),
        tasks={tid: Side(task_start_rating(t.difficulty), TASK_START_RD, 0.06) for tid, t in tasks.items()},
        archived={tid for tid, t in tasks.items() if t.is_archived},
    )
    sessions = list(db.scalars(
        select(ExamSession).where(ExamSession.finished_at.is_not(None)).options(selectinload(ExamSession.answers))
    ))
    # Попытки, которые записал пробник при завершении, — привязать к пробнику.
    exam_windows: dict[tuple[int, int], list[ExamSession]] = {}
    for s in sessions:
        for item in s.answers:
            exam_windows.setdefault((s.user_id, item.task_id), []).append(s)

    def session_of(attempt: Attempt) -> int | None:
        for s in exam_windows.get((attempt.user_id, attempt.task_id), []):
            if s.started_at <= attempt.created_at <= s.finished_at + EXAM_ATTEMPT_WINDOW:
                return s.id
        return None

    events = [_Event(a.created_at, 0, a.id, a.user_id, attempt=a) for a in db.scalars(select(Attempt))]
    events += [_Event(s.finished_at, 1, s.id, s.user_id, session=s) for s in sessions]
    events += [
        _Event(a.created_at, 2, a.id, a.user_id, admin=a)
        for a in db.scalars(select(AdminAction).where(AdminAction.action.in_(PTS_ACTIONS),
                                                      AdminAction.user_id.is_not(None)))
    ]
    events.sort()

    changes: list[PtsChange] = []
    summary = Summary()

    def keep(user_id, task_id, result, score, attempt_id, session_id, at):
        summary.changes.append(abs(result.delta))
        changes.append(PtsChange(
            attempt_id=attempt_id, exam_session_id=session_id, user_id=user_id, task_id=task_id,
            exam_number_id=engine.task_numbers[task_id], score=score,
            pts_before=result.pts_before, pts_after=result.pts_after,
            number_pts_before=result.number_before, number_pts_after=result.number_after,
            task_before=result.task_before, task_after=result.task_after, created_at=at,
        ))

    for event in events:
        if event.attempt is not None:
            a = event.attempt
            task = tasks.get(a.task_id)
            if task is None or session_of(a) is not None:
                continue  # попытку из пробника засчитает сам пробник — в том же порядке, что вживую
            score = score_of(a, task)
            result = engine.play(a.user_id, a.task_id, score)
            if result is not None:
                keep(a.user_id, a.task_id, result, score, a.id, None, a.created_at)
                engine.update_rank(a.user_id)
        elif event.admin is not None:
            engine.apply_admin(event.user_id, event.admin.action, event.admin.details or {}, event.admin.created_at)
        else:
            s = event.session
            for task_id, score in exam_scores(s).items():
                result = engine.play(s.user_id, task_id, score)
                if result is not None:
                    keep(s.user_id, task_id, result, score, None, s.id, s.finished_at)
            engine.users.setdefault(s.user_id, UserState(Side(*_player_start())))
            engine.update_rank(s.user_id, calibrate_at=s.finished_at, calibrate_primary=exam_primary_29(s))

    # Записать: всё старое — стереть, новое — положить.
    db.execute(delete(PtsChange))
    db.execute(delete(UserNumberRating))
    for user in db.scalars(select(User)):
        state = engine.users.get(user.id, UserState(Side(*_player_start())))
        side = state.side
        user.pts, user.pts_rd, user.pts_vol, user.pts_games = side.rating, side.rd, side.vol, side.games
        user.rank_key, user.calibrated_at = state.rank_key, state.calibrated_at
    for tid, task in tasks.items():
        side = engine.tasks[tid]
        task.rating, task.rating_rd, task.rating_vol, task.rating_games = side.rating, side.rd, side.vol, side.games
    for (user_id, number_id), side in engine.numbers.items():
        db.add(UserNumberRating(user_id=user_id, exam_number_id=number_id, pts=side.rating, rd=side.rd,
                                vol=side.vol, games=side.games))
    db.add_all(changes)
    db.commit()

    summary.users = sum(1 for s in engine.users.values() if s.side.games)
    summary.counted = len(changes)
    summary.tasks_rated = sum(1 for s in engine.tasks.values() if s.games)
    return summary


# ---------- Для профиля и отчёта ----------

def user_forecast(db: Session, user: User) -> ranks.Forecast:
    engine = _engine_readonly(db, user)
    return engine.forecast(user.id)


def _engine_readonly(db: Session, user: User) -> Engine:
    active = list(db.scalars(select(Task).where(Task.is_archived.is_(False))))
    engine = Engine(
        task_numbers={t.id: t.exam_number_id for t in active},
        number_codes=_numbers(db),
        tasks={t.id: _side_of_task(t) for t in active},
    )
    engine.users[user.id] = UserState(Side(user.pts, user.pts_rd, user.pts_vol, user.pts_games),
                                      user.rank_key, user.calibrated_at)
    for row in db.scalars(select(UserNumberRating).where(UserNumberRating.user_id == user.id)):
        engine.numbers[(user.id, row.exam_number_id)] = Side(row.pts, row.rd, row.vol, row.games)
    return engine


def exam_delta(db: Session, session_id: int) -> float | None:
    """Сколько PTS дал пробник (None — ни одной задачи не засчитано или это входной пробник:
    после него PTS выставляется калибровкой, а не складывается из ответов)."""
    session = db.get(ExamSession, session_id)
    user = db.get(User, session.user_id) if session else None
    if user is not None and user.calibrated_at is not None and user.calibrated_at == session.finished_at:
        return None
    rows = db.execute(
        select(PtsChange.pts_before, PtsChange.pts_after).where(PtsChange.exam_session_id == session_id)
    ).all()
    return sum(after - before for before, after in rows) if rows else None


@dataclass
class ReportRow:
    user_id: int
    name: str
    games: int
    pts: float
    rd: float
    rank: ranks.Rank | None
    forecast: ranks.Forecast


def report(db: Session) -> list[ReportRow]:
    rows = []
    for u in db.scalars(select(User).where(User.pts_games > 0).order_by(User.pts.desc())):
        rows.append(ReportRow(u.id, u.display_name, u.pts_games, u.pts, u.pts_rd, ranks.by_key(u.rank_key),
                              user_forecast(db, u)))
    return rows


def last_change_at(db: Session) -> datetime | None:
    return db.scalar(select(func.max(PtsChange.created_at)))
