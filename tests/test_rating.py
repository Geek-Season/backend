"""Таблица лидеров по PTS и слабые темы."""

from sqlalchemy import select

from app.modules.tasks.models import Task

TASK_16, TASK_17 = 7, 8


def solve(client, headers, task_id, answer):
    return client.post(f"/tasks/{task_id}/check", json={"answer": answer}, headers=headers).json()["is_correct"]


def uid(client, headers):
    return client.get("/auth/me", headers=headers).json()["id"]


def board(client, headers=None, **params):
    return client.get("/rating", params=params, headers=headers).json()


def test_order_by_pts_only_ranked(client, login, ranked):
    vasya, petya, masha, newbie = login(111, "Вася"), login(222, "Петя"), login(333, "Маша"), login(444, "Новичок")
    ranked(uid(client, vasya), "hero", 1100)
    ranked(uid(client, petya), "legend", 1300)
    ranked(uid(client, masha), "hero", 1200)
    solve(client, newbie, TASK_17, "2 20")  # решал, но без входного пробника — не в таблице

    rating = board(client, petya)
    assert [(r["name"], r["place"]) for r in rating["top"]] == [("Петя", 1), ("Маша", 2), ("Вася", 3)]
    assert rating["participants"] == 3 and rating["counts"] == {"legend": 1, "hero": 2}
    assert [r["is_me"] for r in rating["top"]] == [True, False, False]
    assert rating["me"]["pts"] == 1300 and rating["me"]["place"] == 1  # свой PTS — себе
    assert "1100" not in client.get("/rating").text and "1200" not in client.get("/rating").text  # чужой — нет

    me = board(client, newbie)["me"]
    assert me["ranked"] is False and me["place"] is None and me["pts"] is None
    assert board(client)["me"] is None  # гость


def test_rank_filter_and_immortal_place(client, login, ranked):
    people = [login(100 + i, f"У{i}") for i in range(4)]
    ranked(uid(client, people[0]), "immortal", 1900)
    ranked(uid(client, people[1]), "immortal", 2000)
    ranked(uid(client, people[2]), "legend", 1300)
    ranked(uid(client, people[3]), "legend", 1350)

    rating = board(client)
    assert [r["immortal_place"] for r in rating["top"]] == [1, 2, None, None]
    legends = board(client, people[2], rank="legend")
    assert [(r["name"], r["place"]) for r in legends["top"]] == [("У3", 1), ("У2", 2)]  # место внутри ранга
    assert legends["counts"] == {"immortal": 2, "legend": 2} and legends["me"]["place"] == 2
    assert client.get("/me/rank", headers=people[1]).json()["immortal_place"] == 1
    assert client.get("/me/rank", headers=people[2]).json()["immortal_place"] is None
    assert client.get("/rating?rank=god").status_code == 422


def test_equal_pts_share_place(client, login, ranked):
    a, b, c = login(1, "А"), login(2, "Б"), login(3, "В")
    ranked(uid(client, a), "hero", 1200.2)
    ranked(uid(client, b), "hero", 1199.9)
    ranked(uid(client, c), "hero", 1100)
    assert [r["place"] for r in board(client)["top"]] == [1, 1, 3]


def test_my_row_below_top(client, login, ranked):
    people = [login(100 + i, f"У{i}") for i in range(5)]
    for i, h in enumerate(people):
        ranked(uid(client, h), "hero", 1500 - i * 10)
    me = board(client, people[4], limit=3)["me"]
    assert me["place"] == 5 and me["row"]["name"] == "У4" and me["row"]["is_me"] is True
    assert board(client, people[0], limit=3)["me"]["row"] is None  # в топе — отдельная строка не нужна


def test_next_rank_hint(client, login, ranked):
    me = login()
    ranked(uid(client, me), "hero", 1000)
    hint = board(client, me)["me"]["next_rank"]
    assert hint["rank"]["key"] == "legend" and hint["primary_needed"] >= 1
    ranked(uid(client, me), "immortal", 3000)
    assert board(client, me)["me"]["next_rank"] is None


def test_week_growth(client, login, db, admin_client):
    from tests.test_pts_live import finish_exam

    def new_task(answer):
        return admin_client.post("/tasks", json={"exam_number": "17", "statement": f"Неделя {answer}",
                                                  "correct_answer": answer, "difficulty": 40}).json()["id"]

    vasya, petya = login(111, "Вася"), login(222, "Петя")
    finish_exam(client, db, vasya, {"5", "7"})  # входной: PTS выставлен калибровкой — не прирост
    finish_exam(client, db, petya, set())
    assert board(client, vasya, period="week")["top"] == []
    assert board(client, vasya, period="week")["me"]["growth"] == 0

    first, second = new_task("11"), new_task("22")  # новые задачи: в пробнике их не было
    solve(client, vasya, first, "11")
    solve(client, vasya, second, "22")
    solve(client, petya, first, "0")  # упал — в таблицу недели не попадает
    week = board(client, vasya, period="week")
    assert [r["name"] for r in week["top"]] == ["Вася"]
    growth = week["top"][0]["growth"]
    assert growth > 0 and week["me"]["growth"] == growth and week["me"]["pts"] is None
    assert board(client, petya, period="week")["me"] == {**board(client, petya, period="week")["me"], "place": None}


def test_number_board(client, login, ranked, db):
    vasya, petya = login(111, "Вася"), login(222, "Петя")
    ranked(uid(client, vasya), "hero", 1200)
    ranked(uid(client, petya), "hero", 1200)
    solve(client, vasya, TASK_17, "2 20")
    solve(client, petya, TASK_17, "0")
    ranked(uid(client, petya), "hero", 1500)  # общий PTS выше, но в № 17 Петя слабее
    rating = board(client, petya, number="17")
    assert [r["name"] for r in rating["top"]] == ["Вася", "Петя"]  # по PTS в номере, не по общему
    assert rating["me"]["place"] == 2 and isinstance(rating["me"]["pts"], int)
    assert board(client, petya, number="5")["top"] == []  # в номере 5 ещё не отвечали
    assert client.get("/rating?number=99").status_code == 404
    assert client.get("/rating?number=17&period=week").status_code == 422


def test_blocked_not_in_rating(client, login, ranked, db):
    from datetime import UTC, datetime

    from app.modules.auth.models import User

    me = login()
    ranked(uid(client, me))
    db.get(User, uid(client, me)).blocked_at = datetime.now(UTC)
    db.commit()
    assert board(client)["participants"] == 0


def test_rating_hides_telegram_contacts(client, login, db, ranked):
    """По рейтингу нельзя найти ученика в Telegram: ни @username, ни Telegram ID, ни фамилии."""
    from app.modules.auth.models import User

    vasya = login(111, "Вася")
    user = db.scalar(select(User).where(User.telegram_id == 111))
    user.username, user.last_name = "vasya_secret", "Пупкин"
    db.commit()
    ranked(user.id)

    for headers in (None, vasya):  # гостю и самому ученику
        response = client.get("/rating?period=all", headers=headers)
        assert response.json()["top"][0]["name"] == "Вася"
        for secret in ("vasya_secret", "Пупкин", "111", "username", "telegram"):
            assert secret not in response.text
    assert client.get("/auth/me", headers=vasya).json()["username"] == "vasya_secret"  # себе — видно


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


# ---------- Карточка ученика ----------

def test_student_card_is_public_and_private_enough(client, login, ranked, db):
    from app.modules.auth.models import User

    vasya, petya = login(111, "Вася"), login(222, "Петя")
    user = db.scalar(select(User).where(User.telegram_id == 111))
    user.username, user.last_name, user.nickname = "vasya_secret", "Пупкин", "Котик"
    db.commit()
    ranked(user.id, "immortal", 2000)
    ranked(uid(client, petya), "hero", 1100)
    solve(client, vasya, TASK_17, "2 20")
    solve(client, vasya, TASK_16, "1")
    from app.modules.achievements import service as achievements

    achievements.seed_catalog(db)
    achievements.check_and_grant(db, user.id)  # вживую это делает воркер по событию

    response = client.get(f"/rating/users/{user.id}")  # гостю тоже
    assert response.status_code == 200
    card = response.json()
    assert card["name"] == "Котик" and card["rank"]["key"] == "immortal" and card["immortal_place"] == 1
    assert card["place"] == 1 and card["solved"] == 1 and card["streak"] == 1
    codes = [a["code"] for a in card["achievements"]]
    assert "first_solve" in codes and "rank_immortal" in codes and card["achievements_total"] >= 14
    cal = card["calendar"]
    assert cal["total"] == 2 and cal["active_days"] == 1 and cal["best_streak"] == 1
    assert cal["days"] == [{"date": cal["end"], "attempts": 2, "correct": 1}]
    for secret in ("vasya_secret", "Пупкин", "Вася", "telegram", '"pts"', "forecast", "primary"):
        assert secret not in response.text, secret

    assert client.get(f"/rating/users/{uid(client, petya)}").json()["place"] == 2
    assert client.get("/rating/users/9999").status_code == 404


def test_card_without_rank_and_blocked(client, login, db):
    from datetime import UTC, datetime

    from app.modules.auth.models import User

    me = login()
    card = client.get(f"/rating/users/{uid(client, me)}").json()
    assert card["rank"] is None and card["place"] is None and card["calendar"]["days"] == []
    db.get(User, uid(client, me)).blocked_at = datetime.now(UTC)
    db.commit()
    assert client.get(f"/rating/users/{db.scalar(select(User.id).where(User.telegram_id == 111))}").status_code == 404


def test_best_streak():
    from datetime import date

    from app.modules.progress.service import _best_streak

    days = {date(2026, 10, d) for d in (1, 2, 3, 5, 6, 10, 11, 12, 13)}
    assert _best_streak(days) == 4 and _best_streak(set()) == 0


def test_stats_have_calendar(client, login):
    me = login()
    solve(client, me, TASK_17, "2 20")
    cal = client.get("/me/stats", headers=me).json()["calendar"]
    assert cal["total"] == 1 and cal["streak"] == 1 and len(cal["days"]) == 1
