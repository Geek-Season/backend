"""Домашки: конструктор и отправка, единственная попытка, сдача, разборы, таблица результатов.

Отдельно — что ответ в домашке пишется одной транзакцией: двойной клик не начисляет
PTS дважды, а ошибка посередине не оставляет ни попытки, ни строки домашки.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.main import app
from app.modules.auth.models import User
from app.modules.homework.models import HomeworkAnswer
from app.modules.progress.models import Attempt
from app.modules.pts import service as pts
from app.modules.pts.models import PtsChange
from app.modules.tasks import service as tasks_service
from app.modules.tasks.models import Task
from tests.test_parts import game_task
from tests.test_pts_live import me_id


def make_task(admin_client, statement="Тестовая задача", answer="42", number="1", explanation="Разбор") -> int:
    response = admin_client.post("/tasks", json={
        "exam_number": number, "statement": statement, "correct_answer": answer, "explanation": explanation,
    })
    assert response.status_code == 201
    return response.json()["id"]


def make_homework(admin_client, task_ids, user_ids, send=True, title="Рекурсия: разогрев") -> int:
    response = admin_client.post("/admin/homework", json={
        "title": title, "comment": "Сначала первую", "task_ids": task_ids, "user_ids": user_ids,
    })
    assert response.status_code == 201, response.text
    homework_id = response.json()["id"]
    if send:
        assert admin_client.post(f"/admin/homework/{homework_id}/send").status_code == 200
    return homework_id


def answer(client, me, homework_id, task_id, value="42"):
    return client.post(f"/homework/{homework_id}/tasks/{task_id}/answer", json={"answer": value}, headers=me)


def count(db, model, *where) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model).where(*where))


@pytest.fixture
def student(client, login):
    me = login(111, "Вася")
    return me, me_id(client, me)


# ---------- Доступ ----------

def test_access(client, admin_client, login, student, new_task):
    me, my_id = student
    other = login(222, "Петя")
    draft = make_homework(admin_client, [new_task["id"]], [my_id], send=False)
    sent = make_homework(admin_client, [new_task["id"]], [my_id])

    assert client.get("/homework").status_code == 401
    assert client.get("/admin/homework", headers=me).status_code == 403
    assert client.post("/admin/homework", json={"title": "x"}, headers=me).status_code == 403
    # Черновик ученик не видит, чужую домашку — тоже.
    assert [h["id"] for h in client.get("/homework", headers=me).json()] == [sent]
    assert client.get(f"/homework/{draft}", headers=me).status_code == 404
    assert client.get(f"/homework/{sent}", headers=other).status_code == 404
    assert answer(client, other, sent, new_task["id"]).status_code == 404
    assert client.post(f"/homework/{sent}/submit", headers=other).status_code == 404
    assert client.get("/homework", headers=other).json() == []


def test_admin_can_receive_homework(admin_client, new_task):
    admin_id = admin_client.get("/auth/me").json()["id"]
    homework_id = make_homework(admin_client, [new_task["id"]], [admin_id])
    assert [h["id"] for h in admin_client.get("/homework").json()] == [homework_id]
    assert admin_client.post(f"/homework/{homework_id}/tasks/{new_task['id']}/answer", json={"answer": "42"}).json()["is_correct"]


# ---------- Конструктор ----------

def test_create_edit_send(client, admin_client, login, student):
    me, my_id = student
    petya = me_id(client, login(222, "Петя"))
    t1, t2, t3 = (make_task(admin_client, f"Задача {i}") for i in range(3))

    created = admin_client.post("/admin/homework", json={
        "title": " Маски ", "comment": "", "task_ids": [t1, t2], "user_ids": [my_id, my_id],
    })
    assert created.status_code == 201
    body = created.json()
    assert body["title"] == "Маски" and body["comment"] is None and body["sent_at"] is None
    assert [t["id"] for t in body["tasks"]] == [t1, t2]
    assert [s["id"] for s in body["students"]] == [my_id]
    homework_id = body["id"]

    # Черновик меняется целиком: порядок задач, ученики.
    patched = admin_client.patch(f"/admin/homework/{homework_id}", json={
        "task_ids": [t3, t1], "user_ids": [petya], "comment": "Подвох во второй",
    }).json()
    assert [t["id"] for t in patched["tasks"]] == [t3, t1]
    assert [s["id"] for s in patched["students"]] == [petya]
    assert patched["comment"] == "Подвох во второй"

    row = admin_client.get("/admin/homework").json()[0]
    assert row["sent_at"] is None and row["tasks_count"] == 2 and row["students_count"] == 1
    assert row["numbers"] == ["1"]

    sent = admin_client.post(f"/admin/homework/{homework_id}/send").json()
    assert sent["sent_at"] is not None
    assert admin_client.post(f"/admin/homework/{homework_id}/send").status_code == 409

    # После отправки — только название и комментарий.
    assert admin_client.patch(f"/admin/homework/{homework_id}", json={"task_ids": [t1]}).status_code == 409
    assert admin_client.patch(f"/admin/homework/{homework_id}", json={"user_ids": [my_id]}).status_code == 409
    assert admin_client.patch(f"/admin/homework/{homework_id}", json={"title": "Маски и IP"}).json()["title"] == "Маски и IP"


def test_invalid_homework(admin_client, student, new_task):
    _, my_id = student
    post = lambda **body: admin_client.post("/admin/homework", json={"title": "Д", **body})  # noqa: E731
    assert post(task_ids=[new_task["id"], new_task["id"]]).status_code == 422
    assert post(task_ids=[999999]).status_code == 422
    assert post(user_ids=[999999]).status_code == 422
    assert post(title="").status_code == 422
    # Пустую домашку не отправить.
    empty = post().json()["id"]
    assert admin_client.post(f"/admin/homework/{empty}/send").status_code == 422
    no_students = post(task_ids=[new_task["id"]]).json()["id"]
    assert admin_client.post(f"/admin/homework/{no_students}/send").status_code == 422
    assert admin_client.get("/admin/homework/999999").status_code == 404


def test_picker_search_and_tried_by(client, admin_client, student):
    me, my_id = student
    solved = make_task(admin_client, "Найдите маску сети")
    fresh = make_task(admin_client, "Найдите адрес сети")
    make_task(admin_client, "Другое условие")
    client.post(f"/tasks/{solved}/check", json={"answer": "42"}, headers=me)

    page = admin_client.get("/admin/homework/tasks", params={"number": "1", "q": "найдите", "user_ids": [my_id]}).json()
    assert page["total"] == 2
    tried = {t["id"]: t["tried_by"] for t in page["items"]}
    assert tried == {solved: [my_id], fresh: []}
    # Спецсимволы LIKE ищутся как обычный текст.
    assert admin_client.get("/admin/homework/tasks", params={"q": "100%"}).json()["total"] == 0


def test_random_skips_tried_and_selected(client, admin_client, student, db):
    me, my_id = student
    ids = [t for t in db.scalars(select(Task.id).join(Task.exam_number).where(Task.exam_number.has(code="1")))]
    ids += [make_task(admin_client, f"Ещё {i}") for i in range(5)]
    client.post(f"/tasks/{ids[0]}/check", json={"answer": "нет"}, headers=me)  # пробовал, но неверно

    for _ in range(5):
        picked = admin_client.post("/admin/homework/random", json={
            "number": "1", "count": 3, "user_ids": [my_id], "exclude_task_ids": [ids[1]],
        }).json()
        assert len(picked) == 3
        assert not {t["id"] for t in picked} & {ids[0], ids[1]}
    assert admin_client.post("/admin/homework/random", json={"number": "99"}).status_code == 422


# ---------- Ответы ----------

def test_answer_counts_like_normal_task(client, admin_client, student, db, events):
    me, my_id = student
    task_id = make_task(admin_client)
    homework_id = make_homework(admin_client, [task_id], [my_id])

    result = answer(client, me, homework_id, task_id)
    assert result.status_code == 200
    body = result.json()
    assert body["is_correct"] is True and body["pts_delta"] is not None and body["pts_repeat"] is False
    assert "correct_answer" not in body and "explanation" not in body  # разбор — только после сдачи

    assert count(db, Attempt, Attempt.user_id == my_id, Attempt.task_id == task_id) == 1
    assert count(db, PtsChange, PtsChange.user_id == my_id, PtsChange.task_id == task_id) == 1
    assert ("task.answered", {"user_id": my_id, "task_id": task_id, "is_correct": True, "first_solve": True}) in events
    # Как обычная задача: засчитана в статистике номера.
    numbers = {n["code"]: n["solved"] for n in client.get("/numbers", headers=me).json()}
    assert numbers["1"] == 1

    card = client.get("/homework", headers=me).json()[0]
    assert (card["answered"], card["correct"], card["total"]) == (1, 1, 1)


def test_only_one_attempt(client, admin_client, student, db):
    me, my_id = student
    task_id = make_task(admin_client)
    homework_id = make_homework(admin_client, [task_id], [my_id])

    assert answer(client, me, homework_id, task_id, "7").json()["is_correct"] is False
    assert answer(client, me, homework_id, task_id, "42").status_code == 409
    assert count(db, Attempt, Attempt.user_id == my_id) == 1
    item = client.get(f"/homework/{homework_id}", headers=me).json()["items"][0]
    assert (item["answer"], item["is_correct"]) == ("7", False)


def test_answer_errors(client, admin_client, student, new_task):
    me, my_id = student
    other_task = make_task(admin_client)
    homework_id = make_homework(admin_client, [new_task["id"]], [my_id])
    assert answer(client, me, homework_id, other_task).status_code == 404  # задачи нет в домашке
    wrong_shape = client.post(
        f"/homework/{homework_id}/tasks/{new_task['id']}/answer", json={"answers": {"19": "1"}}, headers=me
    )
    assert wrong_shape.status_code == 422
    # Неверный формат — не попытка: ответить всё ещё можно.
    assert answer(client, me, homework_id, new_task["id"]).status_code == 200


def test_game_task_parts(client, admin_client, student, db):
    me, my_id = student
    game = game_task(client)["id"]
    homework_id = make_homework(admin_client, [game], [my_id])

    body = client.post(
        f"/homework/{homework_id}/tasks/{game}/answer",
        json={"answers": {"19": "29", "20": "48 28", "21": ""}}, headers=me,
    ).json()
    assert body["is_correct"] is False
    assert [(p["number"], p["is_correct"]) for p in body["parts"]] == [("19", True), ("20", False), ("21", False)]
    attempt = db.scalar(select(Attempt).where(Attempt.user_id == my_id))
    assert attempt.answer == "19: 29; 20: 48 28; 21: —"
    item = client.get(f"/homework/{homework_id}", headers=me).json()["items"][0]
    assert item["parts"][0] == {"number": "19", "answer": "29", "is_correct": True}


# ---------- Сдача ----------

def test_submit_with_skips_and_solutions(client, admin_client, student, db):
    me, my_id = student
    t1 = make_task(admin_client, explanation="Считаем: 6·7")
    t2 = make_task(admin_client)
    homework_id = make_homework(admin_client, [t1, t2], [my_id])
    answer(client, me, homework_id, t1)

    before = client.get(f"/homework/{homework_id}", headers=me).json()
    assert all(item["solution"] is None for item in before["items"])
    assert client.get("/homework", headers=me).json()[0]["submitted_at"] is None

    submitted = client.post(f"/homework/{homework_id}/submit", headers=me)
    assert submitted.status_code == 200
    body = submitted.json()
    assert body["submitted_at"] is not None
    assert (body["answered"], body["correct"], body["total"]) == (1, 1, 2)
    assert body["items"][0]["solution"]["explanation"] == "Считаем: 6·7"
    assert body["items"][1]["solution"]["correct_answer"] == "42"
    assert body["items"][1]["is_correct"] is None  # пропуск: в счёте — неверно, но попытки нет

    assert count(db, Attempt, Attempt.user_id == my_id, Attempt.task_id == t2) == 0
    assert answer(client, me, homework_id, t2).status_code == 409
    assert client.post(f"/homework/{homework_id}/submit", headers=me).status_code == 409


def test_list_order(client, admin_client, student, new_task):
    me, my_id = student
    first = make_homework(admin_client, [new_task["id"]], [my_id], title="Первая")
    second = make_homework(admin_client, [new_task["id"]], [my_id], title="Вторая")
    third = make_homework(admin_client, [new_task["id"]], [my_id], title="Третья")
    client.post(f"/homework/{third}/submit", headers=me)
    assert [h["id"] for h in client.get("/homework", headers=me).json()] == [second, first, third]


# ---------- Админ: результаты, ученики, удаление ----------

def test_results_matrix(client, admin_client, login, student):
    me, my_id = student
    petya_headers = login(222, "Петя")
    petya = me_id(client, petya_headers)
    t1, t2 = make_task(admin_client), make_task(admin_client)
    homework_id = make_homework(admin_client, [t1, t2], [my_id, petya])
    answer(client, me, homework_id, t1)
    answer(client, me, homework_id, t2, "0")
    client.post(f"/homework/{homework_id}/submit", headers=me)

    results = admin_client.get(f"/admin/homework/{homework_id}/results").json()
    assert [t["task_id"] for t in results["tasks"]] == [t1, t2]
    vasya, petya_row = results["rows"]
    assert vasya["student"]["display_name"] == "Вася"
    assert [(c["answer"], c["is_correct"]) for c in vasya["cells"]] == [("42", True), ("0", False)]
    assert (vasya["answered"], vasya["correct"]) == (2, 1) and vasya["submitted_at"] is not None
    assert petya_row["cells"] == [None, None] and petya_row["submitted_at"] is None

    row = admin_client.get("/admin/homework").json()[0]
    assert (row["students_count"], row["submitted_count"]) == (2, 1)


def test_add_students_after_send(client, admin_client, login, student, new_task):
    me, my_id = student
    petya_headers = login(222, "Петя")
    petya = me_id(client, petya_headers)
    homework_id = make_homework(admin_client, [new_task["id"]], [my_id])

    added = admin_client.post(f"/admin/homework/{homework_id}/students", json={"user_ids": [petya, my_id]})
    assert [s["id"] for s in added.json()["students"]] == [my_id, petya]
    assert [h["id"] for h in client.get("/homework", headers=petya_headers).json()] == [homework_id]
    assert admin_client.post(f"/admin/homework/{homework_id}/students", json={"user_ids": [999999]}).status_code == 422


def test_delete_keeps_attempts(client, admin_client, student, db, new_task):
    me, my_id = student
    homework_id = make_homework(admin_client, [new_task["id"]], [my_id])
    answer(client, me, homework_id, new_task["id"])

    assert admin_client.delete(f"/admin/homework/{homework_id}").status_code == 204
    assert client.get(f"/homework/{homework_id}", headers=me).status_code == 404
    assert client.get("/homework", headers=me).json() == []
    assert count(db, HomeworkAnswer) == 0
    assert count(db, Attempt, Attempt.user_id == my_id) == 1
    assert admin_client.delete(f"/admin/homework/{homework_id}").status_code == 404


def test_reset_stats_lets_redo_homework(client, admin_client, login, student, db):
    """После сброса статистики ответы в домашке стёрты, «сдано» снято — можно решить заново."""
    me, my_id = student
    other = login(222, "Петя")
    petya = me_id(client, other)
    task_id = make_task(admin_client)
    homework_id = make_homework(admin_client, [task_id], [my_id, petya])
    answer(client, me, homework_id, task_id)
    answer(client, other, homework_id, task_id)
    client.post(f"/homework/{homework_id}/submit", headers=me)

    assert admin_client.post(f"/admin/users/{my_id}/reset-stats").status_code == 200

    detail = client.get(f"/homework/{homework_id}", headers=me).json()
    assert detail["submitted_at"] is None and detail["answered"] == 0
    assert detail["items"][0]["solution"] is None  # разбор снова закрыт до сдачи
    retry = answer(client, me, homework_id, task_id)
    assert retry.status_code == 200 and retry.json()["pts_delta"] is not None  # PTS снова засчитываются
    # Чужие ответы не тронуты.
    rows = {r["student"]["id"]: r for r in admin_client.get(f"/admin/homework/{homework_id}/results").json()["rows"]}
    assert rows[petya]["answered"] == 1
    assert count(db, Attempt, Attempt.user_id == my_id) == 1


# ---------- Задачи: архив и удаление ----------

def test_archived_task_stays_in_homework(client, admin_client, student):
    me, my_id = student
    task_id = make_task(admin_client)
    homework_id = make_homework(admin_client, [task_id], [my_id])

    assert admin_client.delete(f"/tasks/{task_id}").status_code == 204  # в архив — можно
    items = client.get(f"/homework/{homework_id}", headers=me).json()["items"]
    assert [i["task"]["id"] for i in items] == [task_id]
    body = answer(client, me, homework_id, task_id).json()
    assert body["is_correct"] is True
    # Архивную задачу в конструктор уже не добавить.
    assert admin_client.post("/admin/homework", json={"title": "Д", "task_ids": [task_id]}).status_code == 422
    assert task_id not in [t["id"] for t in admin_client.get("/admin/homework/tasks", params={"number": "1"}).json()["items"]]


def test_task_in_homework_cannot_be_deleted_from_db(admin_client, student, db):
    _, my_id = student
    task_id = make_task(admin_client)
    make_homework(admin_client, [task_id], [my_id], send=False)
    with pytest.raises(IntegrityError):
        db.execute(delete(Task).where(Task.id == task_id))
        db.flush()
    db.rollback()
    assert db.get(Task, task_id) is not None


# ---------- Одна транзакция ----------

def test_double_click_counts_once(client, admin_client, student, db, events, monkeypatch):
    """Два одинаковых ответа одновременно: ровно один принят, PTS начислены один раз."""
    me, my_id = student
    task_id = make_task(admin_client)
    homework_id = make_homework(admin_client, [task_id], [my_id])

    # Первый запрос держит транзакцию открытой, пока второй уже пришёл.
    real = tasks_service.record_user_answer

    def slow(*args, **kwargs):
        result = real(*args, **kwargs)
        time.sleep(0.5)
        return result

    monkeypatch.setattr(tasks_service, "record_user_answer", slow)

    barrier = threading.Barrier(2)
    codes: list[int] = []

    def click():
        clicker = TestClient(app, headers=me)
        barrier.wait()
        codes.append(clicker.post(f"/homework/{homework_id}/tasks/{task_id}/answer", json={"answer": "42"}).status_code)

    threads = [threading.Thread(target=click) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert sorted(codes) == [200, 409]
    assert count(db, HomeworkAnswer) == 1
    assert count(db, Attempt, Attempt.user_id == my_id, Attempt.task_id == task_id) == 1
    assert count(db, PtsChange, PtsChange.user_id == my_id, PtsChange.task_id == task_id) == 1
    assert [e for e, _ in events].count("task.answered") == 1


def test_failure_in_the_middle_rolls_back_everything(client, admin_client, student, db, events, monkeypatch):
    """PTS упали после записи попытки — не остаётся ни попытки, ни ответа в домашке."""
    me, my_id = student
    task_id = make_task(admin_client)
    homework_id = make_homework(admin_client, [task_id], [my_id])
    pts_before = db.get(User, my_id).pts

    def broken(*args, **kwargs):
        raise RuntimeError("PTS недоступны")

    monkeypatch.setattr(pts, "record_answer", broken)
    with pytest.raises(RuntimeError):
        answer(client, me, homework_id, task_id)

    assert count(db, HomeworkAnswer) == 0
    assert count(db, Attempt, Attempt.user_id == my_id) == 0
    assert db.get(User, my_id).pts == pts_before
    assert events == []

    monkeypatch.undo()
    retry = answer(client, me, homework_id, task_id)
    assert retry.status_code == 200 and retry.json()["pts_delta"] is not None
