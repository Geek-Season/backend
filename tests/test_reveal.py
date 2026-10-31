"""Ответ и разбор открываются после верного ответа или трёх неверных."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

from app.core.config import settings


@pytest.fixture
def task(admin_client):
    return admin_client.post("/tasks", json={
        "exam_number": "1", "statement": "Сколько?", "correct_answer": "42", "explanation": "Считаем: 6·7",
    }).json()


def answer(client, task_id, value, headers=None):
    return client.post(f"/tasks/{task_id}/check", json={"answer": value}, headers=headers).json()


def items(client, headers=None):
    return {t["id"]: t for t in client.get("/numbers/1/tasks", headers=headers).json()["items"]}


def test_wrong_answer_hides_solution(client, task):
    body = answer(client, task["id"], "1")
    assert body["is_correct"] is False
    assert body["correct_answer"] is None and body["explanation"] is None
    assert "42" not in str(body) and "6·7" not in str(body)
    assert body == {**body, "solution_available": False, "attempts_left": 2}
    assert client.get(f"/tasks/{task['id']}/solution").status_code == 403


def test_right_answer_shows_solution(client, task):
    body = answer(client, task["id"], "42")
    assert body["correct_answer"] == "42" and body["explanation"] == "Считаем: 6·7"
    assert body["solution_available"] is True and body["attempts_left"] == 0
    assert client.get(f"/tasks/{task['id']}/solution").json()["correct_answer"] == "42"


def test_student_opens_solution_after_three_mistakes(client, login, task):
    me = login()
    assert [answer(client, task["id"], v, me)["attempts_left"] for v in ("1", "2")] == [2, 1]
    assert items(client, me)[task["id"]]["solution_available"] is False
    denied = client.get(f"/tasks/{task['id']}/solution", headers=me)
    assert denied.status_code == 403 and "1 неверного ответа" in denied.json()["detail"]

    third = answer(client, task["id"], "3", me)
    assert third["solution_available"] is True and third["attempts_left"] == 0
    assert third["correct_answer"] is None  # сама проверка ответ не раскрывает — только по кнопке
    solution = client.get(f"/tasks/{task['id']}/solution", headers=me).json()
    assert solution == {"task_id": task["id"], "correct_answer": "42", "explanation": "Считаем: 6·7", "parts": []}
    # После F5 кнопка остаётся: признак приходит и в списке задач, и в самой задаче.
    assert items(client, me)[task["id"]]["solution_available"] is True
    assert client.get(f"/tasks/{task['id']}", headers=me).json()["solution_available"] is True

    # Посмотрел ответ и ввёл его — засчитывается как обычно.
    assert answer(client, task["id"], "42", me)["is_correct"] is True
    assert items(client, me)[task["id"]]["status"] == "solved"


def test_counters_are_per_student(client, login, task):
    vasya, petya = login(111, "Вася"), login(222, "Петя")
    for v in ("1", "2", "3"):
        answer(client, task["id"], v, vasya)
    assert client.get(f"/tasks/{task['id']}/solution", headers=vasya).status_code == 200
    assert client.get(f"/tasks/{task['id']}/solution", headers=petya).status_code == 403
    assert items(client, petya)[task["id"]]["solution_available"] is False


def test_solved_before_opens_solution(client, login, task):
    me = login()
    answer(client, task["id"], "42", me)
    later = answer(client, task["id"], "7", me)  # решил раньше, сейчас ошибся
    assert later["solution_available"] is True
    assert client.get(f"/tasks/{task['id']}/solution", headers=me).status_code == 200


def test_guest_counted_by_address(client, task):
    for v in ("1", "2", "3"):
        last = answer(client, task["id"], v)
    assert last["solution_available"] is True
    assert client.get(f"/tasks/{task['id']}/solution").status_code == 200
    assert items(client)[task["id"]]["solution_available"] is True
    other = TestClient(app, client=("10.9.9.9", 50000))  # другой гость — счётчик свой
    assert other.get(f"/tasks/{task['id']}/solution").status_code == 403
    assert items(other)[task["id"]]["solution_available"] is False


def test_threshold_from_settings(client, task, monkeypatch):
    monkeypatch.setattr(settings, "reveal_after_wrong", 1)
    assert answer(client, task["id"], "1")["solution_available"] is True


def test_admin_always_sees_solution(admin_client, task):
    assert admin_client.get(f"/tasks/{task['id']}/solution").json()["explanation"] == "Считаем: 6·7"


def test_game_task_solution_has_parts(admin_client, client):
    parts = [
        {"number": n, "question": f"Вопрос {n}", "correct_answer": a, "explanation": f"Разбор {n}"}
        for n, a in (("19", "10"), ("20", "11 12"), ("21", "13"))
    ]
    task = admin_client.post("/tasks", json={"exam_number": "19-21", "statement": "Игра", "parts": parts}).json()
    for _ in range(3):
        body = client.post(f"/tasks/{task['id']}/check", json={"answers": {"19": "10", "20": "1", "21": ""}}).json()
    assert body["parts"][0]["correct_answer"] == "10" and body["parts"][1]["correct_answer"] is None
    solution = client.get(f"/tasks/{task['id']}/solution").json()
    assert [p["correct_answer"] for p in solution["parts"]] == ["10", "11 12", "13"]
    assert solution["parts"][2]["explanation"] == "Разбор 21"


def test_solution_404(client):
    assert client.get("/tasks/999999/solution").status_code == 404
