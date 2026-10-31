"""Номера 19–21: одно условие игры и три вопроса (части) с отдельными ответами."""

from sqlalchemy import select

from app.modules.progress.models import Attempt
from app.modules.tasks.models import ExamNumber, Task, TaskPart

RIGHT = {"19": "29", "20": "28 48", "21": "44"}


def game_task(client) -> dict:
    page = client.get("/numbers/19-21/tasks", params={"sort": "old"}).json()
    return page["items"][0]


def check(client, task_id, body, headers=None):
    return client.post(f"/tasks/{task_id}/check", json=body, headers=headers)


# ---------- Ученик ----------

def test_parts_shown_without_answers(client):
    task = game_task(client)
    assert [p["number"] for p in task["parts"]] == ["19", "20", "21"]
    assert all("correct_answer" not in p for p in task["parts"])
    assert "Ваня выиграл своим первым ходом" in task["parts"][0]["question"]
    assert client.get("/numbers/8/tasks").json()["items"][0]["parts"] == []


def test_all_parts_right(client):
    result = check(client, game_task(client)["id"], {"answers": {"19": "29", "20": "28  48", "21": " 44 "}})
    assert result.status_code == 200
    body = result.json()
    assert body["is_correct"] is True
    assert [p["is_correct"] for p in body["parts"]] == [True, True, True]
    assert body["correct_answer"] == "19: 29; 20: 28 48; 21: 44"


def test_each_part_checked_separately(client):
    body = check(client, game_task(client)["id"], {"answers": {"19": "29", "20": "48 28", "21": ""}}).json()
    assert body["is_correct"] is False
    assert [p["is_correct"] for p in body["parts"]] == [True, False, False]  # порядок чисел важен; пусто — неверно
    # Верная часть — с разбором; неверные — без ответа и разбора, пока ученик не ошибётся трижды.
    assert body["parts"][0]["explanation"].startswith("Петя неудачно")
    assert body["parts"][1]["correct_answer"] is None and body["parts"][1]["explanation"] is None
    assert body["correct_answer"] is None and body["attempts_left"] == 2


def test_wrong_answer_format(client):
    game_id = game_task(client)["id"]
    simple_id = client.get("/numbers/8/tasks").json()["items"][0]["id"]
    assert check(client, game_id, {"answer": "29"}).status_code == 422
    assert check(client, simple_id, {"answers": RIGHT}).status_code == 422
    assert check(client, game_id, {}).status_code == 422
    assert check(client, game_id, {"answer": "1", "answers": RIGHT}).status_code == 422


def test_solved_only_when_all_parts_right(client, login, db):
    me = login()
    game_id = game_task(client)["id"]
    check(client, game_id, {"answers": {**RIGHT, "21": "1"}}, me)
    assert client.get("/me/stats", headers=me).json()["solved_tasks"] == 0
    check(client, game_id, {"answers": RIGHT}, me)
    assert client.get("/me/stats", headers=me).json()["solved_tasks"] == 1

    answers = db.scalars(select(Attempt.answer).where(Attempt.task_id == game_id).order_by(Attempt.id)).all()
    assert answers == ["19: 29; 20: 28 48; 21: 1", "19: 29; 20: 28 48; 21: 44"]
    numbers = {n["code"]: n for n in client.get("/numbers", headers=me).json()}
    assert numbers["19-21"]["solved"] == 1


# ---------- Пробник ----------

def test_exam_has_three_positions_with_own_answers(client, login, db):
    me = login()
    exam = client.post("/exam/start", headers=me).json()
    items = [i for i in exam["items"] if i["exam_number"] in RIGHT]
    assert [i["exam_number"] for i in items] == ["19", "20", "21"]
    assert len({i["task_id"] for i in items}) == 1  # одна задача — одно общее условие
    assert all(i["question"] and i["points"] == 1 for i in items)

    statement = db.get(Task, items[0]["task_id"]).statement
    right = {p.number: p.correct_answer for p in db.scalars(select(TaskPart).where(TaskPart.task_id == items[0]["task_id"]))}
    for item in items[:2]:  # 19 и 20 верно, 21 без ответа
        client.put(f"/exam/{exam['id']}/answers/{item['position']}", json={"answer": right[item["exam_number"]]}, headers=me)

    result = client.post(f"/exam/{exam['id']}/finish", headers=me).json()
    assert result["primary_score"] == 2
    done = [i for i in result["items"] if i["exam_number"] in RIGHT]
    assert [i["is_correct"] for i in done] == [True, True, False]
    assert [i["correct_answer"] for i in done] == [right["19"], right["20"], right["21"]]
    assert done[0]["statement"] == statement

    # В статистику — одна попытка на задачу, нерешённая (21 не отвечен).
    attempts = db.scalars(select(Attempt).where(Attempt.task_id == items[0]["task_id"])).all()
    assert len(attempts) == 1 and attempts[0].is_correct is False
    assert attempts[0].answer == f"19: {right['19']}; 20: {right['20']}; 21: —"


def test_full_variant_is_29_points_and_exact(client, login, db):
    """Если в банке есть все номера, вариант — 27 позиций и 29 баллов, тестовый балл точный."""
    have = set(db.scalars(select(Task.exam_number_id)))
    for number in db.scalars(select(ExamNumber)):
        if number.id not in have:
            db.add(Task(exam_number=number, statement=f"Задача {number.code}", correct_answer="1"))
    db.commit()

    me = login()
    exam = client.post("/exam/start", headers=me).json()
    assert len(exam["items"]) == 27
    assert [i["exam_number"] for i in exam["items"]] == [str(n) for n in range(1, 28)]
    assert exam["max_score"] == 29
    result = client.post(f"/exam/{exam['id']}/finish", headers=me).json()
    assert result["test_score_approximate"] is False


# ---------- Редактор ----------

PARTS = [
    {"number": "19", "question": "Вопрос 19", "correct_answer": "10"},
    {"number": "20", "question": "Вопрос 20", "correct_answer": "11 12"},
    {"number": "21", "question": "Вопрос 21", "correct_answer": "13", "explanation": "Так"},
]


def test_admin_creates_game_task(admin_client, client):
    response = admin_client.post("/tasks", json={"exam_number": "19-21", "statement": "Игра", "parts": PARTS})
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["correct_answer"] == "19: 10; 20: 11 12; 21: 13"
    assert task["parts"][2] == {"number": "21", "question": "Вопрос 21", "correct_answer": "13", "explanation": "Так"}
    assert check(client, task["id"], {"answers": {"19": "10", "20": "11 12", "21": "13"}}).json()["is_correct"]


def test_admin_game_task_validation(admin_client):
    bad = [
        {"exam_number": "19-21", "statement": "Игра", "correct_answer": "1"},           # без частей
        {"exam_number": "19-21", "statement": "Игра", "parts": PARTS[:2]},              # двух мало
        {"exam_number": "19-21", "statement": "Игра", "parts": [*PARTS[:2], {**PARTS[2], "number": "22"}]},
        {"exam_number": "5", "statement": "Задача", "correct_answer": "1", "parts": PARTS},  # части не у 19–21
        {"exam_number": "5", "statement": "Задача"},                                    # без ответа
    ]
    for body in bad:
        assert admin_client.post("/tasks", json=body).status_code == 422, body


def test_admin_edits_parts(admin_client, client):
    task = admin_client.post("/tasks", json={"exam_number": "19-21", "statement": "Игра", "parts": PARTS}).json()
    changed = [{**p, "correct_answer": "7"} if p["number"] == "19" else p for p in PARTS]
    response = admin_client.patch(f"/tasks/{task['id']}", json={"parts": changed})
    assert response.status_code == 200
    assert response.json()["correct_answer"].startswith("19: 7;")

    # Перенести игру в обычный номер без ответа нельзя — и задача не испортилась.
    assert admin_client.patch(f"/tasks/{task['id']}", json={"exam_number": "5"}).status_code == 422
    after = admin_client.get(f"/tasks/{task['id']}/admin").json()
    assert after["exam_number"] == "19-21" and len(after["parts"]) == 3

    # Изменить только условие — части остаются.
    assert admin_client.patch(f"/tasks/{task['id']}", json={"statement": "Новая игра"}).status_code == 200
    assert len(client.get(f"/tasks/{task['id']}").json()["parts"]) == 3
