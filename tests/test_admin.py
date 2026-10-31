"""Админка: список учеников, правки профиля и PTS, блокировка, роль, журнал."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.progress.models import Attempt
from app.modules.pts import service as pts
from tests.test_pts_live import TASK_17, check, finish_exam, me_id


@pytest.fixture
def student(client, login):
    headers = login(111, "Вася")
    return headers, me_id(client, headers)


def card(admin_client, user_id):
    return admin_client.get(f"/admin/users/{user_id}").json()


def replay_keeps(db, user_id):
    """Пересчёт PTS с нуля (как при запуске сервера) — вернуть (PTS, ранг, откалиброван ли)."""
    pts.recompute_all(db)
    db.expire_all()
    user = db.get(User, user_id)
    return round(user.pts), user.rank_key, user.calibrated_at is not None


def test_only_admins(client, student):
    headers, user_id = student
    assert client.get("/admin/users", headers=headers).status_code == 403
    assert client.post(f"/admin/users/{user_id}/recalibrate", headers=headers).status_code == 403
    assert client.get("/admin/actions").status_code == 401


def test_list_search_and_filters(admin_client, client, login):
    login(111, "Вася")
    login(222, "Петя")
    names = [row["display_name"] for row in admin_client.get("/admin/users").json()["items"]]
    assert {"Вася", "Петя", "Админ"} <= set(names)

    found = admin_client.get("/admin/users", params={"q": "пет"}).json()
    assert found["total"] == 1 and found["items"][0]["display_name"] == "Петя"
    assert admin_client.get("/admin/users", params={"q": "222"}).json()["items"][0]["telegram_id"] == 222

    admins = admin_client.get("/admin/users", params={"filter": "admins"}).json()["items"]
    assert [row["display_name"] for row in admins] == ["Админ"]
    assert admin_client.get("/admin/users", params={"filter": "blocked"}).json()["total"] == 0
    assert admin_client.get("/admin/users", params={"sort": "bogus"}).status_code == 422


def test_set_pts_gives_rank_and_survives_replay(admin_client, client, db, student):
    headers, user_id = student
    check(client, headers, TASK_17, "2 20")
    body = admin_client.post(f"/admin/users/{user_id}/pts", json={"pts": 1500}).json()
    assert body["pts"] == 1500 and body["rank"] is not None and body["calibrated_at"]
    assert body["forecast_primary"] is not None
    assert body["actions"][0]["text"].startswith("изменил PTS:")
    assert "→ 1500" in body["actions"][0]["text"]

    # ранг виден и самому ученику; ручная правка переживает пересчёт при запуске
    assert client.get("/auth/me", headers=headers).json()["rank"]["key"] == body["rank"]["key"]
    assert replay_keeps(db, user_id) == (1500, body["rank"]["key"], True)

    # ответы после правки считаются от нового PTS — и в пересчёте тоже
    check(client, headers, 1, "0")
    db.expire_all()
    live = round(db.get(User, user_id).pts)
    assert live != 1500
    assert replay_keeps(db, user_id)[0] == live


@pytest.mark.parametrize("rank_key", ["recruit", "hero", "divine", "immortal"])
def test_set_rank(admin_client, db, student, rank_key):
    _, user_id = student
    body = admin_client.post(f"/admin/users/{user_id}/pts", json={"rank": rank_key}).json()
    assert body["rank"]["key"] == rank_key
    assert replay_keeps(db, user_id)[1] == rank_key


def test_pts_needs_exactly_one(admin_client, student):
    _, user_id = student
    assert admin_client.post(f"/admin/users/{user_id}/pts", json={}).status_code == 422
    assert admin_client.post(f"/admin/users/{user_id}/pts", json={"pts": 1, "rank": "hero"}).status_code == 422
    assert admin_client.post(f"/admin/users/{user_id}/pts", json={"rank": "god"}).status_code == 422
    assert admin_client.post(f"/admin/users/{user_id}/pts", json={"pts": -5}).status_code == 422


def test_recalibrate_makes_next_exam_entrance(admin_client, client, db, student):
    headers, user_id = student
    finish_exam(client, db, headers, set())  # почти пустой входной → Рекрут
    assert card(admin_client, user_id)["rank"]["key"] == "recruit"

    body = admin_client.post(f"/admin/users/{user_id}/recalibrate").json()
    assert body["rank"] is None and body["calibrated_at"] is None
    assert "сбросил калибровку" in body["actions"][0]["text"]
    assert replay_keeps(db, user_id)[1:] == (None, False)
    # второй раз — нечего сбрасывать
    assert admin_client.post(f"/admin/users/{user_id}/recalibrate").status_code == 422

    result = finish_exam(client, db, headers, {"2", "4", "5", "7", "8", "9", "10", "11", "12", "13", "14"})
    assert result["calibrated_now"] is True and result["rank_up"]["key"] != "recruit"
    db.expire_all()
    live = db.get(User, user_id)
    assert replay_keeps(db, user_id) == (round(live.pts), live.rank_key, True)


def test_reset_stats(admin_client, client, db, fake_redis, student):
    headers, user_id = student
    check(client, headers, TASK_17, "2 20")
    finish_exam(client, db, headers, {"5"})
    assert client.get("/rating?period=all").json()["participants"] == 1

    body = admin_client.post(f"/admin/users/{user_id}/reset-stats").json()
    assert (body["pts"], body["games"], body["solved"], body["attempts"], body["exams"]) == (1000, 0, 0, 0, 0)
    assert body["rank"] is None and body["achievements"] == 0
    assert "сбросил всю статистику" in body["actions"][0]["text"]
    assert db.scalar(select(Attempt.id).where(Attempt.user_id == user_id)) is None
    assert client.get("/rating?period=all").json()["participants"] == 0
    assert replay_keeps(db, user_id) == (1000, None, False)

    # учится заново: ответ снова засчитывается в PTS
    assert check(client, headers, TASK_17, "2 20")["pts_delta"] > 0


def test_block_and_unblock(admin_client, client, db, fake_redis, student):
    headers, user_id = student
    check(client, headers, TASK_17, "2 20")
    body = admin_client.patch(f"/admin/users/{user_id}", json={"blocked": True}).json()
    assert body["blocked"] is True and body["actions"][0]["text"] == "заблокировал"

    me = client.get("/auth/me", headers=headers)
    assert me.status_code == 403 and me.json()["detail"] == "Аккаунт заблокирован"
    assert client.post(f"/tasks/{TASK_17}/check", json={"answer": "1"}, headers=headers).status_code == 403
    assert client.get("/rating?period=all").json()["participants"] == 0
    with pytest.raises(auth_service.AccountBlocked):
        auth_service.issue_code(fake_redis, db, auth_service.TelegramProfile(telegram_id=111, first_name="Вася"))

    assert admin_client.patch(f"/admin/users/{user_id}", json={"blocked": False}).json()["blocked"] is False
    assert client.get("/auth/me", headers=headers).status_code == 200
    assert client.get("/rating?period=all").json()["participants"] == 1


def test_qr_login_blocked(client, db, fake_redis, student, admin_client):
    _, user_id = student
    admin_client.patch(f"/admin/users/{user_id}", json={"blocked": True})
    token = auth_service.start_qr_login(fake_redis)
    with pytest.raises(auth_service.AccountBlocked):
        auth_service.confirm_qr_login(fake_redis, db, token, auth_service.TelegramProfile(telegram_id=111, first_name="Вася"))
    assert client.get(f"/auth/telegram/status/{token}").json()["status"] == "declined"


def test_cannot_block_self_or_admin(admin_client, db, student):
    admin_id = admin_client.get("/auth/me").json()["id"]
    response = admin_client.patch(f"/admin/users/{admin_id}", json={"blocked": True})
    assert response.status_code == 422 and response.json()["detail"] == "Себя заблокировать нельзя"

    _, user_id = student
    admin_client.patch(f"/admin/users/{user_id}", json={"role": "admin"})
    response = admin_client.patch(f"/admin/users/{user_id}", json={"blocked": True})
    assert response.json()["detail"] == "Сначала снимите права администратора"


def test_role(admin_client, client, student):
    headers, user_id = student
    body = admin_client.patch(f"/admin/users/{user_id}", json={"role": "admin"}).json()
    assert body["role"] == "admin" and body["actions"][0]["text"] == "назначил администратором"
    assert client.get("/admin/users", headers=headers).status_code == 200

    # новый админ не может снять права сам с себя, но может снять с другого
    response = client.patch(f"/admin/users/{user_id}", json={"role": "student"}, headers=headers)
    assert response.status_code == 422
    assert admin_client.patch(f"/admin/users/{user_id}", json={"role": "student"}).json()["role"] == "student"
    assert client.get("/admin/users", headers=headers).status_code == 403


def test_profile_fields(admin_client, client, student):
    headers, user_id = student
    body = admin_client.patch(f"/admin/users/{user_id}", json={"nickname": "Котик 42"}).json()
    assert body["display_name"] == "Котик 42"
    assert body["actions"][0]["text"] == "изменил ник: «—» → «Котик 42»"
    assert admin_client.patch(f"/admin/users/{user_id}", json={"nickname": "ab"}).status_code == 422

    body = admin_client.patch(f"/admin/users/{user_id}", json={"nickname": None}).json()
    assert body["nickname"] is None and body["actions"][0]["text"] == "сбросил ник «Котик 42»"

    body = admin_client.patch(f"/admin/users/{user_id}", json={"created_at": "2026-01-15T00:00:00Z"}).json()
    assert body["created_at"].startswith("2026-01-15")
    assert "→ 15.01.2026" in body["actions"][0]["text"]
    assert client.get("/auth/me", headers=headers).json()["created_at"].startswith("2026-01-15")

    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert admin_client.patch(f"/admin/users/{user_id}", json={"created_at": future}).status_code == 422
    assert admin_client.patch(f"/admin/users/{user_id}", json={"bogus": 1}).status_code == 422


def test_old_reset_endpoints_write_journal(admin_client, client, student):
    headers, user_id = student
    client.patch("/auth/me", json={"nickname": "Котик 42"}, headers=headers)
    assert admin_client.delete(f"/auth/users/{user_id}/nickname").status_code == 204
    assert admin_client.delete(f"/auth/users/{user_id}/avatar").status_code == 204
    texts = [a["text"] for a in admin_client.get("/admin/actions", params={"user_id": user_id}).json()["items"]]
    assert texts == ["сбросил аватарку", "сбросил ник «Котик 42»"]


def test_journal_pages_and_names(admin_client, db, student):
    _, user_id = student
    admin = db.scalar(select(User).where(User.telegram_id == 1))
    admin.gender = "f"
    db.commit()
    for day in range(1, 6):
        admin_client.patch(f"/admin/users/{user_id}", json={"created_at": f"2026-0{day}-01T00:00:00Z"})

    first = admin_client.get("/admin/actions", params={"limit": 3}).json()
    assert len(first["items"]) == 3 and first["next_before"]
    assert first["items"][0]["admin"]["name"] == "Админ" and first["items"][0]["user"]["name"] == "Вася"
    assert first["items"][0]["text"].startswith("изменила дату регистрации")
    rest = admin_client.get("/admin/actions", params={"limit": 3, "before": first["next_before"]}).json()
    assert len(rest["items"]) == 2 and rest["next_before"] is None


def test_unknown_user(admin_client):
    assert admin_client.get("/admin/users/9999").status_code == 404
    assert admin_client.post("/admin/users/9999/pts", json={"pts": 5}).status_code == 404
