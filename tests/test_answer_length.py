"""Длинные ответы (до 255 символов): ответы-таблицы номера 25 — 2 × 10–12 чисел в одну строку."""

from sqlalchemy import select

from app.modules.exam.models import ExamAnswer
from app.modules.progress.models import Attempt
from app.modules.tasks.answers import MAX_ANSWER_LENGTH
from tests.test_exam import start
from tests.test_homework import answer as homework_answer
from tests.test_homework import make_homework
from tests.test_pts_live import me_id

# 10 строк «число делитель», как в задаче 25 с kompege: 199 символов.
TABLE_25 = " ".join(f"{89016775 + i * 3911:010d} {901527 + i * 1000}" for i in range(10)) + " 1234567890 12345678"


def make_25(admin_client, answer=TABLE_25):
    return admin_client.post("/tasks", json={"exam_number": "25", "statement": "Маски", "correct_answer": answer})


def test_limit_is_255():
    assert MAX_ANSWER_LENGTH == 255
    assert 150 < len(TABLE_25) <= 255


def test_long_answer_task_created_and_checked(admin_client, client, login, db):
    created = make_25(admin_client)
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    assert admin_client.get(f"/tasks/{task_id}/admin").json()["correct_answer"] == TABLE_25

    me = login()
    result = client.post(f"/tasks/{task_id}/check", json={"answer": TABLE_25}, headers=me).json()
    assert result["is_correct"]
    # Попытка ученика с длинным ответом сохранилась целиком.
    saved = db.scalar(select(Attempt.answer).where(Attempt.user_id == me_id(client, me), Attempt.task_id == task_id))
    assert saved == TABLE_25


def test_answer_over_255_rejected(admin_client, client):
    too_long = "1" * 256
    assert make_25(admin_client, too_long).status_code == 422
    task_id = make_25(admin_client).json()["id"]
    assert client.post(f"/tasks/{task_id}/check", json={"answer": too_long}).status_code == 422
    assert client.post(f"/tasks/{task_id}/check", json={"answer": "1" * 255}).status_code == 200
    patched = admin_client.patch(f"/tasks/{task_id}", json={"correct_answer": too_long})
    assert patched.status_code == 422


def test_long_part_answer(admin_client, client):
    parts = [{"number": n, "question": f"Вопрос {n}", "correct_answer": a}
             for n, a in (("19", "29"), ("20", TABLE_25), ("21", "44"))]
    created = admin_client.post("/tasks", json={"exam_number": "19-21", "statement": "Игра", "parts": parts})
    assert created.status_code == 201, created.text
    task = created.json()
    # Сводка ответов частей больше не режется на 100 символах.
    assert task["correct_answer"] == f"19: 29; 20: {TABLE_25}; 21: 44"
    check = client.post(f"/tasks/{task['id']}/check", json={"answers": {"19": "29", "20": TABLE_25, "21": "44"}})
    assert check.json()["is_correct"]
    assert client.post(f"/tasks/{task['id']}/check", json={"answers": {"20": "1" * 256}}).status_code == 422


def test_long_answer_in_homework(admin_client, client, login):
    task_id = make_25(admin_client).json()["id"]
    me = login()
    homework_id = make_homework(admin_client, [task_id], [me_id(client, me)])
    response = homework_answer(client, me, homework_id, task_id, TABLE_25)
    assert response.status_code == 200, response.text
    assert response.json()["is_correct"]


def test_long_answer_in_exam(client, login, db):
    me = login()
    exam = start(client, me).json()
    saved = client.put(f"/exam/{exam['id']}/answers/1", json={"answer": TABLE_25}, headers=me)
    assert saved.status_code == 200, saved.text
    assert db.scalar(select(ExamAnswer.answer).where(ExamAnswer.answer == TABLE_25)) == TABLE_25
    assert client.put(f"/exam/{exam['id']}/answers/1", json={"answer": "1" * 256}, headers=me).status_code == 422
