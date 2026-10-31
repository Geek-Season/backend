"""PTS, шаг 2: начисление за ответ и пробник, входной пробник, ранг, достижения."""

import pytest
from sqlalchemy import select

from app.modules.achievements import service as achievements
from app.modules.auth.models import User
from app.modules.notifications.service import render
from app.modules.pts import service
from app.modules.pts.models import PtsChange
from app.modules.tasks.models import Task

TASK_17 = 8  # единственная задача № 17 в seed: ответ «2 20»


def check(client, me, task_id, answer):
    return client.post(f"/tasks/{task_id}/check", json={"answer": answer}, headers=me).json()


def me_id(client, me):
    return client.get("/auth/me", headers=me).json()["id"]


def finish_exam(client, db, me, correct_numbers):
    """Пробник: верные ответы только на номера из correct_numbers, остальное — пусто."""
    exam = client.post("/exam/start", headers=me).json()
    answers = dict(db.execute(select(Task.id, Task.correct_answer)).all())
    parts = {}
    for t in db.scalars(select(Task)):
        for p in t.parts:
            parts[(t.id, p.number)] = p.correct_answer
    for item in exam["items"]:
        if item["exam_number"] in correct_numbers:
            right = parts.get((item["task_id"], item["exam_number"])) or answers[item["task_id"]]
            client.put(f"/exam/{exam['id']}/answers/{item['position']}", json={"answer": right}, headers=me)
    return client.post(f"/exam/{exam['id']}/finish", headers=me).json()


def test_answer_gives_and_takes_pts(client, login):
    me = login()
    right = check(client, me, TASK_17, "2 20")
    assert right["pts_delta"] > 0 and right["pts_repeat"] is False
    again = check(client, me, TASK_17, "1")
    assert again["pts_delta"] is None and again["pts_repeat"] is True  # повторная попытка — без PTS

    other = login(222, "Петя")
    wrong = check(client, other, TASK_17, "1")
    assert wrong["pts_delta"] < 0
    assert abs(wrong["pts_delta"]) < right["pts_delta"] * 3  # потери новичка смягчены


def test_guest_gets_no_pts(client):
    body = check(client, {}, TASK_17, "2 20")
    assert body["pts_delta"] is None and body["pts_repeat"] is False


def test_no_rank_before_entrance_exam(client, login):
    me = login()
    check(client, me, TASK_17, "2 20")
    rank = client.get("/me/rank", headers=me).json()
    assert rank["calibrated"] is False and rank["rank"] is None and rank["forecast_primary"] is None
    assert rank["games"] == 1 and rank["pts"] > 1000
    assert client.get("/auth/me", headers=me).json()["rank"] is None


def test_entrance_exam_sets_rank_from_its_score(client, login, db):
    me = login()
    result = finish_exam(client, db, me, {"2", "4", "5", "7", "8", "9", "10", "11"})
    assert result["calibrated_now"] is True
    # 8 первичных из max_score → приведено к 29
    target = result["primary_score"] * 29 / result["max_score"]
    rank = client.get("/me/rank", headers=me).json()
    assert rank["calibrated"] is True
    assert abs(rank["forecast_primary"] - target) <= 1
    assert result["rank_up"]["key"] == rank["rank"]["key"]
    assert client.get("/auth/me", headers=me).json()["rank"]["key"] == rank["rank"]["key"]
    assert result["pts_delta"] is None  # входной: PTS выставлен калибровкой, а не суммой ответов

    second = finish_exam(client, db, me, set())  # задачи уже встречались — PTS не меняется
    assert second["calibrated_now"] is False and second["rank_up"] is None


def test_blank_exam_answers_count_as_wrong(client, login, db):
    me = login()
    finish_exam(client, db, me, set())
    changes = db.scalars(select(PtsChange).where(PtsChange.user_id == me_id(client, me))).all()
    assert changes and all(c.score == 0 for c in changes)
    assert all(c.exam_session_id is not None for c in changes)
    assert client.get("/me/rank", headers=me).json()["rank"]["key"] == "recruit"


def test_rank_up_after_answer(client, login, db, admin_client):
    me = login()
    finish_exam(client, db, me, {"2", "4", "5", "7", "8", "9", "10", "11"})
    user = db.get(User, me_id(client, me))
    real_rank = user.rank_key
    assert real_rank != "recruit"
    user.rank_key = "recruit"  # будто раньше был ниже — ответ должен поднять ранг до прогноза
    db.commit()
    new = admin_client.post("/tasks", json={"exam_number": "1", "statement": "Новая", "correct_answer": "5"}).json()
    body = check(client, me, new["id"], "5")
    assert body["pts_delta"] > 0
    assert body["rank_up"]["key"] == real_rank
    assert check(client, me, new["id"], "5")["rank_up"] is None


def test_rank_achievement_and_telegram_text(client, login, db):
    me = login()
    finish_exam(client, db, me, {"2", "4", "5", "7", "8", "9", "10", "11", "13", "14", "15", "16", "17"})
    user = db.get(User, me_id(client, me))
    granted = {a.code for a in achievements.check_and_grant(db, user.id)}
    expected = {"rank_knight", "rank_hero", "rank_legend", "rank_overlord", "rank_divine", "rank_immortal"}
    level = {"recruit": 0, "knight": 1, "hero": 2, "legend": 3, "overlord": 4, "divine": 5, "immortal": 6}[user.rank_key]
    assert granted & expected == {f"rank_{k}" for k, v in level_map().items() if 1 <= v <= level}
    text = render({"event": "achievement.granted", "code": "rank_hero", "icon": "🔁",
                   "title": "Герой рекурсии", "description": "Ранг по прогнозу: от 51 тестового балла"})
    assert text.startswith("🔁 Новый ранг: <b>Герой рекурсии</b>!")


def level_map():
    return {"knight": 1, "hero": 2, "legend": 3, "overlord": 4, "divine": 5, "immortal": 6}


def test_rating_shows_only_rank(client, login, db):
    me = login()
    finish_exam(client, db, me, {"5"})
    check(client, me, TASK_17, "2 20")
    row = client.get("/rating?period=all").json()["top"][0]
    assert row["rank"]["key"] and row["rank"]["short"]
    text = client.get("/rating?period=all").text
    assert "pts" not in text.lower() and "forecast" not in text


def test_replay_matches_live(client, login, db):
    """Пересчёт с нуля даёт то же, что ученик получил вживую."""
    vasya, petya = login(111, "Вася"), login(222, "Петя")
    check(client, vasya, TASK_17, "2 20")
    finish_exam(client, db, vasya, {"5", "7"})
    check(client, petya, TASK_17, "0")
    finish_exam(client, db, petya, set())
    db.expire_all()
    live = {u.id: (round(u.pts, 6), u.rank_key, u.pts_games) for u in db.scalars(select(User).where(User.pts_games > 0))}
    service.recompute_all(db)
    db.expire_all()
    replay = {u.id: (round(u.pts, 6), u.rank_key, u.pts_games) for u in db.scalars(select(User).where(User.pts_games > 0))}
    assert replay == live


@pytest.mark.parametrize("exam_score, rank_key", [(0, "recruit"), (29, "immortal")])
def test_anchor_extremes(exam_score, rank_key):
    codes = [str(i) for i in range(1, 19)] + ["19-21", "22", "23", "24", "25", "26", "27"]
    engine = service.Engine(task_numbers={i: i for i in range(len(codes))}, number_codes=dict(enumerate(codes)),
                            tasks={i: service.Side(1000, 50, 0.06) for i in range(len(codes))})
    engine.play(7, 0, 1.0)
    engine.anchor(7, exam_score)
    assert engine.forecast(7).rank.key == rank_key


@pytest.mark.parametrize("exam_score", [3.0, 8.5, 15.0, 22.0])
def test_anchor_hits_target(exam_score):
    codes = [str(i) for i in range(1, 19)] + ["19-21", "22", "23", "24", "25", "26", "27"]
    engine = service.Engine(task_numbers={i: i for i in range(len(codes))}, number_codes=dict(enumerate(codes)),
                            tasks={i: service.Side(900 + 30 * i, 50, 0.06) for i in range(len(codes))})
    for i in range(5):
        engine.play(7, i, 1.0 if i % 2 else 0.0)
    engine.anchor(7, exam_score)
    assert engine.forecast(7).primary_exact == pytest.approx(exam_score, abs=0.01)
