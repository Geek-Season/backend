"""PTS, шаг 1: Glicko-2, правила начисления, пересчёт по истории, прогноз балла и ранги."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.progress.models import Attempt
from app.modules.pts import glicko, ranks, service
from app.modules.pts.models import PtsChange, UserNumberRating
from app.modules.tasks.models import ExamNumber, Task, TaskPart

# ---------- Glicko-2 ----------


def test_glickman_paper_example():
    """Пример из статьи Гликмана: 1500/200/0.06 против трёх соперников → 1464.06 / 151.52 / 0.05999."""
    new = glicko.rate(glicko.Rating(1500, 200, 0.06), [
        (glicko.Rating(1400, 30), 1), (glicko.Rating(1550, 100), 0), (glicko.Rating(1700, 300), 0),
    ])
    assert new.rating == pytest.approx(1464.06, abs=0.01)
    assert new.rd == pytest.approx(151.52, abs=0.01)
    assert new.vol == pytest.approx(0.05999, abs=1e-5)


def test_expected_matches_elo():
    assert glicko.expected(1500, 1500) == pytest.approx(0.5)
    assert glicko.expected(1500, 1900) == pytest.approx(0.0909, abs=1e-3)


def side(rating, rd=65.0, games=100):
    return service.Side(rating, rd, 0.06, games)


@pytest.mark.parametrize("task_rating, win, loss", [(1100, 2, -22), (1500, 12, -12), (1900, 22, -2)])
def test_table_from_the_plan(task_rating, win, loss):
    """Откалиброванный ученик 1500 против откалиброванной задачи — числа из плана (±2 PTS)."""
    for score, expected_delta in ((1.0, win), (0.0, loss)):
        user = side(1500)
        service.play(user, side(1500), side(task_rating, rd=50), score)
        assert user.rating - 1500 == pytest.approx(expected_delta, abs=3)


def test_harder_task_gives_more():
    easy, hard = side(1500), side(1500)
    service.play(easy, side(1500), side(1200), 1.0)
    service.play(hard, side(1500), side(1800), 1.0)
    assert hard.rating - 1500 > easy.rating - 1500 > 0


def test_calibrating_task_halves_change():
    calibrated, fresh = side(1500), side(1500)
    service.play(calibrated, side(1500), side(1500, games=100), 1.0)
    service.play(fresh, side(1500), side(1500, games=0), 1.0)
    assert (fresh.rating - 1500) == pytest.approx((calibrated.rating - 1500) / 2, rel=0.02)


def test_newbie_loses_half():
    newbie, veteran = side(1500, games=0), side(1500, games=100)
    service.play(newbie, side(1500), side(1500), 0.0)
    service.play(veteran, side(1500), side(1500), 0.0)
    assert (1500 - newbie.rating) == pytest.approx((1500 - veteran.rating) / 2, rel=0.02)


def test_pts_never_negative():
    user = side(5, rd=350, games=100)
    service.play(user, side(5), side(0), 0.0)
    assert user.rating == 0


def test_task_moves_opposite():
    task = side(1500, rd=50)
    service.play(side(1500), side(1500), task, 1.0)
    assert task.rating < 1500  # задачу решили — она «дешевеет»
    assert task.games == 101


def test_task_start_from_difficulty():
    assert service.task_start_rating(30) == 1000 and service.task_start_rating(80) == 1500


# ---------- Ранги и прогноз ----------

@pytest.mark.parametrize("primary, key", [(0, "recruit"), (5, "recruit"), (6, "knight"), (9, "knight"), (10, "hero"),
                                          (14, "legend"), (18, "overlord"), (21, "divine"), (26, "immortal"), (29, "immortal")])
def test_rank_borders(primary, key):
    assert ranks.rank_for(primary).key == key


def test_rank_borders_match_test_scores():
    from app.modules.exam.scale import to_test_score
    assert [to_test_score(r.min_primary) for r in reversed(ranks.RANKS)] == [0, 40, 51, 62, 72, 80, 93]


def test_forecast_example_from_plan():
    """Уверенно № 1–15 (шанс 0,85), остальное — 0,3: ≈ 17 первичных → 70 баллов → Легенда экселя."""
    codes = [str(i) for i in range(1, 19)] + ["19-21", "22", "23", "24", "25", "26", "27"]
    typical = dict.fromkeys(codes, 1500.0)
    strong, weak = 1500 + 400 * 0.7533, 1500 - 400 * 0.3680  # шанс 0,85 и 0,3
    number_pts = {c: (strong if c.isdigit() and int(c) <= 15 else weak, 5) for c in codes}
    f = ranks.forecast(number_pts, typical)
    assert f.primary_exact == pytest.approx(16.95, abs=0.05)
    assert (f.primary, f.test_score, f.rank.title) == (17, 70, "Легенда экселя")


def test_untried_numbers_count_zero():
    typical = {"1": 1000.0, "2": 1000.0}
    f = ranks.forecast({"1": (3000.0, 5), "2": (3000.0, 2)}, typical)  # по № 2 только 2 ответа
    assert f.primary == 1


# ---------- Пересчёт по истории ----------

def add_attempt(db, user, task, answer, ok, minutes):
    db.add(Attempt(user_id=user.id, task_id=task.id, answer=answer, is_correct=ok,
                   created_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minutes)))


@pytest.fixture
def people(db):
    vasya = User(telegram_id=1001, first_name="Вася")
    petya = User(telegram_id=1002, first_name="Петя")
    db.add_all([vasya, petya])
    db.commit()
    return vasya, petya


def tasks_of(db, code, at_least=1):
    """Задачи номера; если их меньше at_least — досоздать (в seed по номеру 1 задач нет)."""
    number = db.scalar(select(ExamNumber).where(ExamNumber.code == code))
    tasks = list(db.scalars(select(Task).where(Task.exam_number_id == number.id).order_by(Task.id)))
    for i in range(len(tasks), at_least):
        tasks.append(Task(exam_number=number, statement=f"Задача {code}-{i}", correct_answer=str(i + 1), difficulty=30))
        db.add(tasks[-1])
    db.commit()
    return tasks


def test_recompute_counts_first_attempt_only(db, people):
    vasya, petya = people
    task = tasks_of(db, "17")[0]
    add_attempt(db, vasya, task, "1", False, 0)
    add_attempt(db, vasya, task, "2 20", True, 1)  # повтор — без PTS
    add_attempt(db, petya, task, "2 20", True, 2)
    db.commit()
    summary = service.recompute_all(db)
    assert summary.attempts == 2 and summary.users == 2
    db.refresh(vasya), db.refresh(petya)
    assert vasya.pts < 1000 < petya.pts
    assert vasya.pts_games == 1
    changes = db.scalars(select(PtsChange).order_by(PtsChange.created_at)).all()
    assert [c.score for c in changes] == [0.0, 1.0]
    assert changes[0].task_after > changes[0].task_before  # Вася ошибся — задача «подорожала»


def test_recompute_is_repeatable(db, people):
    vasya, petya = people
    for i, task in enumerate(tasks_of(db, "1", 3)[:3]):
        add_attempt(db, vasya, task, task.correct_answer, True, i)
        add_attempt(db, petya, task, "0", False, i + 10)
    db.commit()
    service.recompute_all(db)
    first = [(u.pts, u.pts_rd) for u in db.scalars(select(User).order_by(User.id))]
    service.recompute_all(db)
    assert [(u.pts, u.pts_rd) for u in db.scalars(select(User).order_by(User.id))] == first
    assert db.scalar(select(UserNumberRating).where(UserNumberRating.user_id == vasya.id)).games == 3
    assert len(db.scalars(select(PtsChange)).all()) == 6


def test_game_task_partial_score(db, people):
    vasya, _ = people
    task = tasks_of(db, "19-21")[0]
    right = {p.number: p.correct_answer for p in task.parts}
    add_attempt(db, vasya, task, f"19: {right['19']}; 20: {right['20']}; 21: —", False, 0)
    db.commit()
    service.recompute_all(db)
    assert db.scalar(select(PtsChange)).score == pytest.approx(2 / 3)


def test_score_of_falls_back_to_is_correct():
    task = Task(statement="", correct_answer="x", parts=[TaskPart(number="19", question="", correct_answer="1")])
    assert service.score_of(Attempt(answer="непонятно", is_correct=True), task) == 1.0


def test_report_and_forecast(db, people):
    vasya, _ = people
    for i, task in enumerate(tasks_of(db, "1", 3)[:3]):
        add_attempt(db, vasya, task, task.correct_answer, True, i)
    db.commit()
    service.recompute_all(db)
    [row] = service.report(db)
    assert row.name == "Вася" and row.games == 3
    assert 0 < row.forecast.primary_exact <= 1  # уверенно только № 1 — до 1 первичного
    assert row.forecast.rank.key == "recruit"


def test_command_prints_report(db, people, capsys):
    from app.modules.pts.__main__ import main
    vasya, _ = people
    task = tasks_of(db, "1", 1)[0]
    add_attempt(db, vasya, task, task.correct_answer, True, 0)
    db.commit()
    main()
    out = capsys.readouterr().out
    assert "учеников 1" in out and "Вася" in out and "Рекрут ворда" in out
