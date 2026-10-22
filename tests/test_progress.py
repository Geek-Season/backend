"""Сохранение ответов и статистика ученика."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.config import settings
from app.modules.auth import service as auth_service
from app.modules.auth.service import TelegramProfile
from app.modules.progress.models import Attempt
from app.modules.progress.service import _streak
from app.seed import TASKS as SEED_TASKS

TASK_17 = 8  # единственная задача номера 17 из seed: ответ "2 20"
TASK_16 = 7  # номер 16: ответ "89"


def login(client, fake_redis, db, telegram_id=111, name="Вася") -> dict:
    profile = TelegramProfile(telegram_id=telegram_id, first_name=name)
    code = auth_service.issue_code(fake_redis, db, profile)
    token = client.post("/auth/code", json={"code": code}).json()["access_token"]
    fake_redis.flushall()  # сбросить счётчик попыток ввода кода
    return {"Authorization": f"Bearer {token}"}


def answer(client, task_id, text, headers=None):
    response = client.post(f"/tasks/{task_id}/check", json={"answer": text}, headers=headers or {})
    assert response.status_code == 200
    return response.json()["is_correct"]


# ---------- Сохранение ответов ----------

def test_anonymous_answers_are_not_saved(client, db):
    assert answer(client, TASK_17, "2 20") is True
    assert db.query(Attempt).count() == 0
    by_code = {n["code"]: n for n in client.get("/numbers").json()}
    assert by_code["17"]["solved"] == 0
    assert client.get("/numbers/17/tasks").json()["items"][0]["status"] is None


def test_wrong_then_right(client, fake_redis, db):
    me = login(client, fake_redis, db)

    assert answer(client, TASK_17, "20 2", me) is False
    assert client.get("/numbers/17/tasks", headers=me).json()["items"][0]["status"] == "failed"

    assert answer(client, TASK_17, "2 20", me) is True
    page = client.get("/numbers/17/tasks", headers=me).json()
    assert page["items"][0]["status"] == "solved"
    assert page["number"]["solved"] == 1

    by_code = {n["code"]: n for n in client.get("/numbers", headers=me).json()}
    assert by_code["17"]["solved"] == 1
    assert by_code["16"]["solved"] == 0


def test_stats(client, fake_redis, db):
    me = login(client, fake_redis, db)
    answer(client, TASK_17, "20 2", me)
    answer(client, TASK_17, "2 20", me)
    answer(client, TASK_17, "2 20", me)  # повтор решённой задачи — «решено» не удваивается
    answer(client, TASK_16, "100", me)

    stats = client.get("/me/stats", headers=me).json()
    assert stats["total_attempts"] == 4
    assert stats["correct_attempts"] == 2
    assert stats["accuracy"] == 0.5
    assert stats["solved_tasks"] == 1
    assert stats["attempted_tasks"] == 2
    assert stats["total_tasks"] == len(SEED_TASKS)
    assert stats["streak_days"] == 1
    assert stats["active_days"] == 1

    assert len(stats["activity"]) == 14
    assert stats["activity"][-1] == {
        "date": datetime.now(ZoneInfo(settings.timezone)).date().isoformat(),
        "attempts": 4,
        "correct": 2,
    }
    assert sum(d["attempts"] for d in stats["activity"]) == 4

    by_code = {n["code"]: n for n in stats["by_number"]}
    assert by_code["17"] == {"code": "17", "title": "Пары/тройки чисел", "tasks_count": 1, "attempted": 1, "solved": 1}
    assert by_code["16"]["attempted"] == 1 and by_code["16"]["solved"] == 0

    assert stats["recent"][0]["task_id"] == TASK_16
    assert stats["recent"][0]["is_correct"] is False
    assert len(stats["recent"]) == 4


def test_empty_stats(client, fake_redis, db):
    me = login(client, fake_redis, db)
    stats = client.get("/me/stats", headers=me).json()
    assert stats["total_attempts"] == 0
    assert stats["accuracy"] is None
    assert stats["streak_days"] == 0
    assert stats["recent"] == []
    assert all(d["attempts"] == 0 for d in stats["activity"])


def test_stats_require_login(client):
    assert client.get("/me/stats").status_code == 401


def test_users_do_not_see_each_other(client, fake_redis, db):
    vasya = login(client, fake_redis, db, 111, "Вася")
    petya = login(client, fake_redis, db, 222, "Петя")
    answer(client, TASK_17, "2 20", vasya)

    assert client.get("/me/stats", headers=petya).json()["total_attempts"] == 0
    assert client.get("/numbers/17/tasks", headers=petya).json()["items"][0]["status"] is None


def test_archived_task_not_counted_as_solved(client, fake_redis, db, admin_client):
    me = login(client, fake_redis, db)
    answer(client, TASK_17, "2 20", me)
    admin_client.delete(f"/tasks/{TASK_17}")

    stats = client.get("/me/stats", headers=me).json()
    assert stats["solved_tasks"] == 0
    assert stats["total_tasks"] == len(SEED_TASKS) - 1
    assert stats["total_attempts"] == 1  # сама попытка в истории остаётся


def test_bad_token_on_open_endpoint_is_ignored(client, db):
    """Устаревший токен не ломает решение задач — ответ просто не сохраняется."""
    assert answer(client, TASK_17, "2 20", {"Authorization": "Bearer old"}) is True
    assert db.query(Attempt).count() == 0


# ---------- Дни и серия ----------

def test_day_uses_moscow_time(client, fake_redis, db):
    """00:30 по Москве — это ещё вчера по UTC, но в статистике должен быть «сегодня»."""
    me = login(client, fake_redis, db)
    user_id = client.get("/auth/me", headers=me).json()["id"]
    tz = ZoneInfo(settings.timezone)
    today = datetime.now(tz).date()
    just_after_midnight = datetime.combine(today, time(0, 30), tzinfo=tz)

    db.add(Attempt(user_id=user_id, task_id=TASK_16, answer="89", is_correct=True,
                   created_at=just_after_midnight))
    db.commit()

    activity = client.get("/me/stats", headers=me).json()["activity"]
    assert activity[-1]["attempts"] == 1


@pytest.mark.parametrize(
    ("days_ago", "expected"),
    [
        ([], 0),
        ([0], 1),
        ([0, 1, 2], 3),
        ([1, 2], 2),        # сегодня ещё не решал — серия со вчера не сгорает
        ([0, 2, 3], 1),     # пропуск позавчера обрывает серию
        ([2, 3], 0),        # вчера и сегодня пусто — серии нет
    ],
)
def test_streak(days_ago, expected):
    today = date(2026, 10, 22)
    assert _streak({today - timedelta(days=d) for d in days_ago}, today) == expected


# ---------- Фильтр «решённые / нерешённые» ----------

def _ids(client, url, headers):
    response = client.get(url, headers=headers)
    assert response.status_code == 200, response.text
    page = response.json()
    return page["total"], [t["id"] for t in page["items"]]


def test_progress_filter(client, fake_redis, db, admin_client):
    me = login(client, fake_redis, db)
    # В номере 1 три задачи: одну решим, в одной ошибёмся, третью не тронем.
    ids = [
        admin_client.post("/tasks", json={"exam_number": "1", "statement": f"З{i}", "correct_answer": "1"}).json()["id"]
        for i in range(3)
    ]
    solved, failed, untouched = ids
    answer(client, solved, "1", me)
    answer(client, failed, "2", me)

    assert _ids(client, "/numbers/1/tasks?progress=solved", me) == (1, [solved])
    total, unsolved = _ids(client, "/numbers/1/tasks?progress=unsolved&sort=old", me)
    assert total == 2 and unsolved == [failed, untouched]
    assert _ids(client, "/numbers/1/tasks", me)[0] == 3


def test_progress_filter_combines_with_source(client, fake_redis, db):
    me = login(client, fake_redis, db)
    answer(client, 3, "768", me)  # задача номера 7 из демоверсии — решена
    assert _ids(client, "/numbers/7/tasks?progress=solved&source=demo", me)[0] == 1
    assert _ids(client, "/numbers/7/tasks?progress=unsolved&source=demo", me)[0] == 0


def test_progress_filter_needs_login(client):
    assert client.get("/numbers/1/tasks?progress=solved").status_code == 401
    assert client.get("/numbers/1/tasks?progress=whatever").status_code == 422
