"""Рейтинг и слабые темы."""

from sqlalchemy import select

from app.modules.tasks.models import Task

TASK_16, TASK_17 = 7, 8


def solve(client, headers, task_id, answer):
    return client.post(f"/tasks/{task_id}/check", json={"answer": answer}, headers=headers).json()["is_correct"]


def test_points_for_first_solve_only(client, login):
    vasya, petya = login(111, "Вася"), login(222, "Петя")
    solve(client, vasya, TASK_17, "2 20")
    solve(client, vasya, TASK_17, "2 20")  # повтор — без очка
    solve(client, vasya, TASK_16, "89")
    solve(client, vasya, TASK_16, "1")     # неверно — без очка
    solve(client, petya, TASK_17, "2 20")

    for period in ("week", "all"):
        rating = client.get(f"/rating?period={period}", headers=petya).json()
        assert [(r["first_name"], r["points"], r["place"]) for r in rating["top"]] == [("Вася", 2, 1), ("Петя", 1, 2)]
        assert rating["me"] == {"place": 2, "points": 1}
        assert [r["is_me"] for r in rating["top"]] == [False, True]
        assert rating["participants"] == 2


def test_equal_points_share_place(client, login):
    a, b, c = login(1, "А"), login(2, "Б"), login(3, "В")
    solve(client, a, TASK_17, "2 20"); solve(client, a, TASK_16, "89")
    solve(client, b, TASK_17, "2 20")
    solve(client, c, TASK_16, "89")
    places = [r["place"] for r in client.get("/rating", headers=c).json()["top"]]
    assert places == [1, 2, 2]
    assert client.get("/rating", headers=c).json()["me"]["place"] == 2


def test_rating_for_guest_and_newcomer(client, login):
    assert client.get("/rating").json()["me"] is None
    newbie = login(333, "Новичок")
    assert client.get("/rating", headers=newbie).json()["me"] == {"place": None, "points": 0}
    assert client.get("/rating?period=year").status_code == 422


def test_rating_rebuilds_from_database(client, login, fake_redis):
    me = login()
    solve(client, me, TASK_17, "2 20")
    solve(client, me, TASK_16, "89")
    fake_redis.flushall()  # Redis перезапустился без данных
    me = login()           # (вход заново, т.к. flushall стёр и коды)
    rating = client.get("/rating?period=all", headers=me).json()
    assert rating["top"][0]["points"] == 2
    assert client.get("/rating?period=week", headers=me).json()["top"][0]["points"] == 2


def test_exam_gives_points(client, login, db):
    me = login()
    exam = client.post("/exam/start", headers=me).json()
    answers = dict(db.execute(select(Task.id, Task.correct_answer)).all())
    for item in exam["items"][:3]:
        client.put(f"/exam/{exam['id']}/answers/{item['position']}",
                   json={"answer": answers[item["task_id"]]}, headers=me)
    client.post(f"/exam/{exam['id']}/finish", headers=me)
    assert client.get("/rating", headers=me).json()["me"]["points"] == 3


# ---------- Слабые темы ----------

def weak(client, headers):
    return {w["code"]: w for w in client.get("/me/stats", headers=headers).json()["weak_numbers"]}


def test_unsolved_task_makes_number_weak(client, login):
    me = login()
    solve(client, me, TASK_17, "0 0")
    assert weak(client, me)["17"]["unsolved_tasks"] == 1

    solve(client, me, TASK_17, "2 20")  # решил: 2 ответа, 50 % — но ответов меньше 3
    assert "17" not in weak(client, me)


def test_low_accuracy_makes_number_weak(client, login):
    me = login()
    solve(client, me, TASK_16, "1")
    solve(client, me, TASK_16, "2")
    solve(client, me, TASK_16, "89")  # решена, но точность 33 % при 3 ответах
    w = weak(client, me)["16"]
    assert w["unsolved_tasks"] == 0 and w["accuracy"] == 0.333


def test_weak_sorted_worst_first(client, login):
    me = login()
    solve(client, me, TASK_16, "89")
    solve(client, me, TASK_16, "1")
    solve(client, me, TASK_16, "2")    # 16: точность 0.33
    solve(client, me, TASK_17, "0 0")  # 17: точность 0
    assert list(weak(client, me)) == ["17", "16"]
