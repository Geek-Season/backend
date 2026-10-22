"""Проверка API через настоящие HTTP-запросы к приложению."""


# ---------- Страница «Задания» ----------

def test_numbers_in_order_with_counts(client):
    numbers = client.get("/numbers").json()
    assert [n["code"] for n in numbers][:3] == ["1", "2", "3"]
    assert "19-21" in [n["code"] for n in numbers]
    by_code = {n["code"]: n for n in numbers}
    assert by_code["17"]["tasks_count"] == 1
    assert by_code["1"]["tasks_count"] == 0


# ---------- Страница прототипов ----------

def test_number_tasks_hide_answers(client):
    page = client.get("/numbers/17/tasks").json()
    assert page["number"]["title"] == "Пары/тройки чисел"
    assert page["total"] == 1
    task = page["items"][0]
    assert "correct_answer" not in task
    assert "explanation" not in task


def test_unknown_number_is_404(client):
    assert client.get("/numbers/99/tasks").status_code == 404


def test_filter_by_source(client):
    assert client.get("/numbers/7/tasks?source=demo").json()["total"] == 1
    assert client.get("/numbers/7/tasks?source=official").json()["total"] == 0


def test_sort_by_difficulty(client):
    for difficulty in (70, 10, 40):
        client.post(
            "/tasks",
            json={"exam_number": "1", "statement": f"Задача {difficulty}",
                  "correct_answer": "1", "difficulty": difficulty},
        )
    easy = [t["difficulty"] for t in client.get("/numbers/1/tasks?sort=easy").json()["items"]]
    assert easy == [10, 40, 70]


def test_pagination(client):
    for i in range(5):
        client.post("/tasks", json={"exam_number": "1", "statement": f"З{i}", "correct_answer": "1"})
    page = client.get("/numbers/1/tasks?limit=2&offset=4").json()
    assert page["total"] == 5
    assert len(page["items"]) == 1


# ---------- Проверка ответа ----------

def test_check_correct_and_wrong(client, new_task):
    url = f"/tasks/{new_task['id']}/check"
    assert client.post(url, json={"answer": " 42 "}).json()["is_correct"] is True
    wrong = client.post(url, json={"answer": "41"})
    assert wrong.status_code == 200
    assert wrong.json()["is_correct"] is False


def test_check_validation(client, new_task):
    url = f"/tasks/{new_task['id']}/check"
    assert client.post(url, json={"answer": ""}).status_code == 422
    assert client.post(url, json={"answer": 42}).status_code == 422
    assert client.post(url, json={"answer": "1", "is_correct": True}).status_code == 422
    assert client.post("/tasks/999999/check", json={"answer": "1"}).status_code == 404


# ---------- Старые баги ----------

def test_bug1_ids_are_unique_after_delete(client):
    """Раньше id = len(TASKS) + 1 повторялся после удаления."""
    first = client.post("/tasks", json={"exam_number": "1", "statement": "A", "correct_answer": "1"}).json()
    client.delete(f"/tasks/{first['id']}")
    second = client.post("/tasks", json={"exam_number": "1", "statement": "B", "correct_answer": "1"}).json()
    third = client.post("/tasks", json={"exam_number": "1", "statement": "C", "correct_answer": "1"}).json()
    assert len({first["id"], second["id"], third["id"]}) == 3


def test_bug2_text_answer_does_not_crash(client):
    """Раньше ответ «abc» ронял проверку с ошибкой 500."""
    task = client.post(
        "/tasks", json={"exam_number": "2", "statement": "Буквы", "correct_answer": "zyxw"}
    ).json()
    response = client.post(f"/tasks/{task['id']}/check", json={"answer": "ZYXW"})
    assert response.status_code == 200
    assert response.json()["is_correct"] is True


def test_bug3_delete(client, new_task):
    """Раньше удаление несуществующей задачи отвечало 200 null."""
    assert client.delete("/tasks/999999").status_code == 404
    assert client.delete(f"/tasks/{new_task['id']}").status_code == 204
    # Задача ушла в архив: её больше не видно.
    assert client.get(f"/tasks/{new_task['id']}").status_code == 404
    assert client.delete(f"/tasks/{new_task['id']}").status_code == 404
    assert client.get("/numbers/1/tasks").json()["total"] == 0


def test_bug4_patch_updates(client, new_task):
    """Раньше PATCH печатал «okey» и ничего не менял."""
    url = f"/tasks/{new_task['id']}"
    response = client.patch(url, json={"correct_answer": "43", "exam_number": "2"})
    assert response.status_code == 200
    assert response.json()["correct_answer"] == "43"
    assert response.json()["exam_number"] == "2"
    assert response.json()["statement"] == "Тестовая задача"  # не присланное не меняется
    assert client.patch(url, json={"statement": None}).status_code == 422
    assert client.patch(url, json={"exam_number": "99"}).status_code == 422
    assert client.patch("/tasks/999999", json={"difficulty": 1}).status_code == 404


def test_bug5_create_returns_201_with_id(client):
    """Раньше создание отвечало 200 без id."""
    response = client.post(
        "/tasks", json={"exam_number": "1", "statement": "X", "correct_answer": "1"}
    )
    assert response.status_code == 201
    assert isinstance(response.json()["id"], int)


def test_create_validation(client):
    assert client.post(
        "/tasks", json={"exam_number": "99", "statement": "X", "correct_answer": "1"}
    ).status_code == 422
    assert client.post(
        "/tasks", json={"exam_number": "1", "statement": "X", "correct_answer": "1", "difficulty": 101}
    ).status_code == 422
    assert client.post(
        "/tasks", json={"exam_number": "1", "statement": "", "correct_answer": "1"}
    ).status_code == 422
