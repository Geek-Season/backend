"""Пробный экзамен."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.modules.exam.models import ExamSession
from app.modules.progress.models import Attempt
from app.modules.tasks.models import Task
from app.seed import GAME_TASKS
from app.seed import TASKS as SEED_TASKS

# Задача 19–21 занимает в варианте три позиции: 19, 20, 21.
NUMBERS_WITH_TASKS = {t["exam_number"] for t in SEED_TASKS} | ({"19", "20", "21"} if GAME_TASKS else set())
MAX_SCORE = len(NUMBERS_WITH_TASKS) + 2  # 26 и 27 стоят по 2 балла


def correct_answers(db) -> dict[int, str]:
    return dict(db.execute(select(Task.id, Task.correct_answer)).all())


def start(client, me):
    response = client.post("/exam/start", headers=me)
    assert response.status_code in (200, 201), response.text
    return response


def expire(db, exam_id):
    db.execute(update(ExamSession).where(ExamSession.id == exam_id)
               .values(deadline_at=datetime.now(UTC) - timedelta(seconds=1)))
    db.commit()


def test_start_builds_variant_without_answers(client, login):
    me = login()
    response = start(client, me)
    assert response.status_code == 201
    exam = response.json()
    assert [i["position"] for i in exam["items"]] == list(range(1, len(NUMBERS_WITH_TASKS) + 1))
    codes = [i["exam_number"] for i in exam["items"]]
    assert set(codes) == NUMBERS_WITH_TASKS
    at = codes.index("19")
    assert codes[at - 1:at + 4] == ["17", "19", "20", "21", "25"]
    assert exam["max_score"] == MAX_SCORE
    assert exam["finished_at"] is None and exam["primary_score"] is None
    assert all(i["correct_answer"] is None and i["explanation"] is None for i in exam["items"])
    assert exam["duration_minutes"] == 235


def test_start_again_returns_same_exam(client, login):
    me = login()
    first = start(client, me).json()
    second = start(client, me)
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]


def test_answers_survive_reload(client, login, fake_redis):
    me = login()
    exam = start(client, me).json()
    saved = client.put(f"/exam/{exam['id']}/answers/1", json={"answer": " 42 "}, headers=me)
    assert saved.status_code == 200 and saved.json()["answer"] == "42"
    assert saved.json()["correct_answer"] is None  # до завершения не подсказываем

    fake_redis.delete(*fake_redis.keys("exam:active:*"))  # Redis «забыл» — база помнит
    current = client.get("/exam/current", headers=me).json()
    assert current["id"] == exam["id"]
    assert current["items"][0]["answer"] == "42"
    assert current["answered"] == 1

    client.put(f"/exam/{exam['id']}/answers/1", json={"answer": ""}, headers=me)  # стереть
    assert client.get("/exam/current", headers=me).json()["answered"] == 0


def test_finish_scores_and_reveals(client, login, db):
    me = login()
    exam = start(client, me).json()
    answers = correct_answers(db)
    for item in exam["items"]:
        if item["exam_number"] in {"26", "27", "5"}:  # 2 + 2 + 1 балл
            client.put(f"/exam/{exam['id']}/answers/{item['position']}",
                       json={"answer": answers[item["task_id"]]}, headers=me)
        elif item["exam_number"] == "8":
            client.put(f"/exam/{exam['id']}/answers/{item['position']}", json={"answer": "0"}, headers=me)

    result = client.post(f"/exam/{exam['id']}/finish", headers=me).json()
    assert result["primary_score"] == 5
    assert result["finished_at"] is not None
    by_number = {i["exam_number"]: i for i in result["items"]}
    assert by_number["26"]["is_correct"] is True
    assert by_number["8"]["is_correct"] is False
    assert by_number["9"]["is_correct"] is False and by_number["9"]["answer"] is None
    assert all(i["correct_answer"] is not None for i in result["items"])

    # Повторное «Завершить» — тот же результат.
    again = client.post(f"/exam/{exam['id']}/finish", headers=me).json()
    assert again["primary_score"] == 5 and again["finished_at"] == result["finished_at"]

    # Ответы попали в статистику: 4 отвеченные задачи → 4 попытки.
    assert db.query(Attempt).count() == 4
    # После завершения можно начать новый пробник.
    assert start(client, me).json()["id"] != exam["id"]

    history = client.get("/exam/history", headers=me).json()
    assert [h["primary_score"] for h in history] == [5]


def test_no_answers_after_finish(client, login):
    me = login()
    exam = start(client, me).json()
    client.post(f"/exam/{exam['id']}/finish", headers=me)
    response = client.put(f"/exam/{exam['id']}/answers/1", json={"answer": "1"}, headers=me)
    assert response.status_code == 409


def test_time_is_up(client, login, db):
    me = login()
    exam = start(client, me).json()
    client.put(f"/exam/{exam['id']}/answers/1", json={"answer": "123"}, headers=me)
    expire(db, exam["id"])

    late = client.put(f"/exam/{exam['id']}/answers/2", json={"answer": "1"}, headers=me)
    assert late.status_code == 409
    assert "Время вышло" in late.json()["detail"]

    # Пробник завершился сам, с ответами, данными вовремя.
    assert client.get("/exam/current", headers=me).json() is None
    finished = client.get(f"/exam/{exam['id']}", headers=me).json()
    assert finished["finished_at"] is not None
    assert finished["items"][0]["answer"] == "123"
    assert finished["items"][1]["answer"] is None


def test_expired_exam_is_finished_on_start(client, login, db):
    me = login()
    exam = start(client, me).json()
    expire(db, exam["id"])
    new = start(client, me)
    assert new.status_code == 201 and new.json()["id"] != exam["id"]


def test_strangers_cannot_see_or_answer(client, login):
    vasya, petya = login(111, "Вася"), login(222, "Петя")
    exam = start(client, vasya).json()
    assert client.get(f"/exam/{exam['id']}", headers=petya).status_code == 404
    assert client.put(f"/exam/{exam['id']}/answers/1", json={"answer": "1"}, headers=petya).status_code == 404
    assert client.post(f"/exam/{exam['id']}/finish", headers=petya).status_code == 404


def test_validation_and_auth(client, login):
    assert client.post("/exam/start").status_code == 401
    me = login()
    exam = start(client, me).json()
    assert client.put(f"/exam/{exam['id']}/answers/999", json={"answer": "1"}, headers=me).status_code == 404
    assert client.put(f"/exam/{exam['id']}/answers/1", json={"answer": "1" * 101}, headers=me).status_code == 422
    assert client.put(f"/exam/{exam['id']}/answers/1", json={"answer": "1", "is_correct": True}, headers=me).status_code == 422


def test_empty_bank(client, login, db):
    db.execute(update(Task).values(is_archived=True))
    db.commit()
    assert client.post("/exam/start", headers=login()).status_code == 409


def test_double_click_is_blocked(client, login, fake_redis):
    me = login()
    user_id = client.get("/auth/me", headers=me).json()["id"]
    fake_redis.set(f"exam:start_lock:{user_id}", 1)  # как будто первый запрос ещё создаёт пробник
    assert client.post("/exam/start", headers=me).status_code == 409


# ---------- Тестовые баллы ----------

def test_official_scale():
    from app.modules.exam.scale import to_test_score
    assert [to_test_score(p) for p in (0, 1, 6, 8, 18, 29)] == [0, 7, 40, 46, 72, 100]
    assert to_test_score(35) == 100 and to_test_score(-1) == 0


def test_result_has_test_score(client, login, db):
    me = login()
    exam = start(client, me).json()
    assert exam["test_score"] is None  # до завершения не считаем
    answers = correct_answers(db)
    for item in exam["items"]:
        if item["exam_number"] in {"26", "27", "5", "8"}:  # 2 + 2 + 1 + 1 = 6 первичных
            client.put(f"/exam/{exam['id']}/answers/{item['position']}",
                       json={"answer": answers[item["task_id"]]}, headers=me)
    result = client.post(f"/exam/{exam['id']}/finish", headers=me).json()
    assert result["primary_score"] == 6
    assert result["test_score"] == 40
    assert result["passed"] is True and result["university_passed"] is False
    assert result["test_score_approximate"] is True  # в банке не все 27 номеров
    assert client.get("/exam/history", headers=me).json()[0]["test_score"] == 40
