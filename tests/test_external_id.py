"""external_id: импортированную задачу находим по id во внешнем банке, дублей не бывает."""

TASK = {"exam_number": "1", "statement": "Импортированная задача", "correct_answer": "26",
        "source_label": "КЕГЭ (kompege.ru) · Демоверсия 2027", "external_id": "kompege:31498"}


def test_create_and_find(admin_client):
    created = admin_client.post("/tasks", json=TASK)
    assert created.status_code == 201
    assert created.json()["external_id"] == "kompege:31498"

    found = admin_client.get("/tasks/by-external/kompege:31498")
    assert found.status_code == 200
    assert found.json()["id"] == created.json()["id"]
    assert found.json()["correct_answer"] == "26"

    assert admin_client.get("/tasks/by-external/kompege:1").status_code == 404


def test_duplicate_is_conflict(admin_client):
    assert admin_client.post("/tasks", json=TASK).status_code == 201
    again = admin_client.post("/tasks", json={**TASK, "statement": "Другой текст"})
    assert again.status_code == 409


def test_archived_task_still_found(admin_client):
    task_id = admin_client.post("/tasks", json=TASK).json()["id"]
    assert admin_client.delete(f"/tasks/{task_id}").status_code == 204

    # Задача в архиве — но повторный импорт всё равно должен её узнать, а не создать заново.
    assert admin_client.get("/tasks/by-external/kompege:31498").json()["id"] == task_id
    assert admin_client.post("/tasks", json=TASK).status_code == 409


def test_without_external_id_no_conflict(admin_client):
    plain = {k: v for k, v in TASK.items() if k != "external_id"}
    assert admin_client.post("/tasks", json=plain).status_code == 201
    assert admin_client.post("/tasks", json=plain).status_code == 201


def test_external_id_is_admin_only(admin_client, client, login):
    admin_client.post("/tasks", json=TASK)
    assert client.get("/tasks/by-external/kompege:31498").status_code == 401
    assert client.get("/tasks/by-external/kompege:31498", headers=login()).status_code == 403
    # Ученику внешний id не показываем.
    task_id = admin_client.get("/tasks/by-external/kompege:31498").json()["id"]
    assert "external_id" not in client.get(f"/tasks/{task_id}").json()


def test_external_id_cannot_be_changed(admin_client):
    task_id = admin_client.post("/tasks", json=TASK).json()["id"]
    assert admin_client.patch(f"/tasks/{task_id}", json={"external_id": "kompege:2"}).status_code == 422
