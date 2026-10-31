"""Админка: ученики и журнал действий.

Любое изменение ученика отсюда записывается в журнал (admin_actions). Правки PTS
делаются тем же движком, что и пересчёт (pts.service.Engine.apply_admin), поэтому
переживают пересчёт PTS при запуске сервера.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from redis import Redis
from sqlalchemy import delete, distinct, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.achievements.models import UserAchievement
from app.modules.admin.models import AdminAction
from app.modules.auth import avatars, nickname
from app.modules.auth.models import ROLE_ADMIN, User
from app.modules.exam.models import ExamSession
from app.modules.progress.models import Attempt
from app.modules.pts import ranks
from app.modules.pts import service as pts
from app.modules.pts.models import PtsChange, UserNumberRating
from app.modules.rating import service as rating

FILTERS = ("all", "admins", "blocked", "no_rank")
SORTS = ("new", "recent", "pts", "solved", "name")


class AdminError(Exception):
    """Действие нельзя выполнить — текст для админа."""


# ---------- Журнал ----------

def log(db: Session, admin: User, user: User | None, action: str, details: dict | None = None,
        at: datetime | None = None) -> AdminAction:
    """Записать действие (commit — у вызывающего, вместе с самим изменением)."""
    entry = AdminAction(admin_id=admin.id, user_id=user.id if user else None, action=action,
                        details=details or {}, created_at=at or datetime.now(UTC))
    db.add(entry)
    return entry


def _date(value: str | None) -> str:
    return datetime.fromisoformat(value).strftime("%d.%m.%Y") if value else "—"


def _rank_title(key: str | None) -> str:
    rank = ranks.by_key(key)
    return rank.short if rank else "без ранга"


def describe(action: str, details: dict, female: bool = False) -> str:
    """Действие словами, без имени админа: «изменил PTS: 0 → 1200»."""
    def verb(word: str) -> str:
        return word + ("а" if female else "")

    before, after = details.get("before") or {}, details.get("after") or {}
    match action:
        case pts.ACTION_SET_PTS:
            text = f"{verb('изменил')} PTS: {before.get('pts', '?')} → {after.get('pts', '?')}"
            if before.get("rank") != after.get("rank"):
                text += f" (ранг: {_rank_title(before.get('rank'))} → {_rank_title(after.get('rank'))})"
            return text
        case pts.ACTION_SET_RANK:
            return (f"{verb('изменил')} ранг: {_rank_title(before.get('rank'))} → {_rank_title(after.get('rank'))}"
                    f" (PTS {before.get('pts', '?')} → {after.get('pts', '?')})")
        case pts.ACTION_RECALIBRATE:
            return f"{verb('сбросил')} калибровку (был ранг: {_rank_title(before.get('rank'))})"
        case pts.ACTION_RESET_STATS:
            return (f"{verb('сбросил')} всю статистику: задач решено {before.get('solved', 0)}, "
                    f"пробников {before.get('exams', 0)}, PTS {before.get('pts', '?')}")
        case "nickname.set":
            return f"{verb('изменил')} ник: «{before.get('nickname') or '—'}» → «{after.get('nickname')}»"
        case "nickname.reset":
            return f"{verb('сбросил')} ник «{before.get('nickname') or '—'}»"
        case "avatar.reset":
            return f"{verb('сбросил')} аватарку"
        case "created_at.set":
            return (f"{verb('изменил')} дату регистрации: {_date(before.get('created_at'))} → "
                    f"{_date(after.get('created_at'))}")
        case "role.grant":
            return f"{verb('назначил')} администратором"
        case "role.revoke":
            return f"{verb('снял')} права администратора"
        case "user.block":
            return verb("заблокировал")
        case "user.unblock":
            return verb("разблокировал")
    return action


def action_read(entry: AdminAction, people: dict[int, User]) -> dict:
    admin = people.get(entry.admin_id) if entry.admin_id else None
    user = people.get(entry.user_id) if entry.user_id else None
    return {
        "id": entry.id,
        "action": entry.action,
        "text": describe(entry.action, entry.details or {}, female=bool(admin and admin.gender == "f")),
        "details": entry.details or {},
        "created_at": entry.created_at,
        "admin": {"id": admin.id, "name": admin.display_name} if admin else None,
        "user": {"id": user.id, "name": user.display_name} if user else None,
    }


def actions(db: Session, user_id: int | None = None, admin_id: int | None = None,
            before: int | None = None, limit: int = 50) -> dict:
    query = select(AdminAction).order_by(AdminAction.id.desc()).limit(limit + 1)
    if user_id is not None:
        query = query.where(AdminAction.user_id == user_id)
    if admin_id is not None:
        query = query.where(AdminAction.admin_id == admin_id)
    if before is not None:
        query = query.where(AdminAction.id < before)
    entries = list(db.scalars(query))
    more = len(entries) > limit
    entries = entries[:limit]
    ids = {i for e in entries for i in (e.admin_id, e.user_id) if i}
    people = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    return {
        "items": [action_read(e, people) for e in entries],
        "next_before": entries[-1].id if more else None,
    }


# ---------- Список и карточка ----------

def _solved():
    return (
        select(Attempt.user_id, func.count(distinct(Attempt.task_id)).label("solved"))
        .where(Attempt.is_correct.is_(True))
        .group_by(Attempt.user_id)
        .subquery()
    )


def _row(user: User, solved: int) -> dict:
    return {
        "id": user.id,
        "telegram_id": user.telegram_id,
        "display_name": user.display_name,
        "nickname": user.nickname,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "username": user.username,
        "avatar_url": user.avatar_url,
        "role": user.role,
        "blocked": user.is_blocked,
        "rank": user.rank_info,
        "pts": round(user.pts),
        "games": user.pts_games,
        "solved": solved or 0,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
    }


def list_users(db: Session, q: str = "", filter: str = "all", sort: str = "new",
               limit: int = 50, offset: int = 0) -> dict:
    solved = _solved()
    query = select(User, func.coalesce(solved.c.solved, 0)).outerjoin(solved, solved.c.user_id == User.id)

    q = q.strip().lstrip("@")
    if q:
        like = f"%{q}%"
        conditions = [
            User.nickname.ilike(like), User.first_name.ilike(like), User.last_name.ilike(like),
            User.username.ilike(like),
            func.concat(User.first_name, " ", func.coalesce(User.last_name, "")).ilike(like),
        ]
        if q.isdigit():
            conditions += [User.telegram_id == int(q), User.id == int(q)]
        query = query.where(or_(*conditions))

    if filter == "admins":
        query = query.where(User.role == ROLE_ADMIN)
    elif filter == "blocked":
        query = query.where(User.blocked_at.is_not(None))
    elif filter == "no_rank":
        query = query.where(User.rank_key.is_(None))

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    order = {
        "new": [User.created_at.desc()],
        "recent": [User.last_login_at.desc().nulls_last()],
        "pts": [User.pts.desc()],
        "solved": [func.coalesce(solved.c.solved, 0).desc()],
        "name": [func.lower(func.coalesce(User.nickname, User.first_name))],
    }[sort]
    rows = db.execute(query.order_by(*order, User.id.desc()).limit(limit).offset(offset)).all()
    return {"total": total, "items": [_row(user, n) for user, n in rows]}


def get_target(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise LookupError
    return user


def detail(db: Session, user: User) -> dict:
    solved = db.scalar(select(func.count(distinct(Attempt.task_id)))
                       .where(Attempt.user_id == user.id, Attempt.is_correct.is_(True)))
    forecast = pts.user_forecast(db, user) if user.rank_key else None
    return {
        **_row(user, solved),
        "gender": user.gender,
        "has_custom_avatar": user.avatar_custom,
        "pts_rd": round(user.pts_rd),
        "calibrated_at": user.calibrated_at,
        "blocked_at": user.blocked_at,
        "forecast_primary": forecast.primary if forecast else None,
        "forecast_score": forecast.test_score if forecast else None,
        "attempts": db.scalar(select(func.count()).where(Attempt.user_id == user.id)),
        "exams": db.scalar(select(func.count()).where(ExamSession.user_id == user.id,
                                                      ExamSession.finished_at.is_not(None))),
        "achievements": db.scalar(select(func.count()).where(UserAchievement.user_id == user.id)),
        "admin_from_env": user.telegram_id in settings.admin_ids,
        "actions": actions(db, user_id=user.id, limit=10)["items"],
    }


# ---------- Изменения ----------

def _pts_state(user: User) -> dict:
    return {"pts": round(user.pts), "rank": user.rank_key}


def set_nickname(db: Session, admin: User, user: User, value: str | None) -> None:
    try:
        new = nickname.clean(value)
    except nickname.NicknameError as error:
        raise AdminError(str(error)) from None
    if new == user.nickname:
        return
    before = user.nickname
    user.nickname = new
    if new is None:
        log(db, admin, user, "nickname.reset", {"before": {"nickname": before}})
    else:
        log(db, admin, user, "nickname.set", {"before": {"nickname": before}, "after": {"nickname": new}})


def reset_avatar(db: Session, admin: User, user: User) -> None:
    """Сбросить аватарку (commit внутри avatars.forget_custom). Фото из Telegram подтянется в фоне."""
    log(db, admin, user, "avatar.reset")
    avatars.forget_custom(db, user)
    db.commit()


def set_created_at(db: Session, admin: User, user: User, value: datetime) -> None:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if value > datetime.now(UTC):
        raise AdminError("Дата регистрации не может быть в будущем")
    before = user.created_at
    user.created_at = value
    log(db, admin, user, "created_at.set",
        {"before": {"created_at": before.isoformat() if before else None},
         "after": {"created_at": value.isoformat()}})


def set_role(db: Session, admin: User, user: User, role: str) -> None:
    if role == user.role:
        return
    if user.id == admin.id:
        raise AdminError("Свою роль поменять нельзя — попросите другого администратора")
    user.role = role
    log(db, admin, user, "role.grant" if role == ROLE_ADMIN else "role.revoke")


def set_blocked(db: Session, admin: User, user: User, blocked: bool) -> bool:
    """True — что-то изменилось (рейтинг нужно пересобрать)."""
    if blocked == user.is_blocked:
        return False
    if blocked and user.id == admin.id:
        raise AdminError("Себя заблокировать нельзя")
    if blocked and user.is_admin:
        raise AdminError("Сначала снимите права администратора")
    user.blocked_at = datetime.now(UTC) if blocked else None
    log(db, admin, user, "user.block" if blocked else "user.unblock")
    return True


def change_pts(db: Session, admin: User, user: User, value: int | None, rank_key: str | None) -> None:
    if (value is None) == (rank_key is None):
        raise AdminError("Укажите либо PTS, либо ранг")
    at = datetime.now(UTC)
    before = _pts_state(user)
    if value is not None:
        action, params = pts.ACTION_SET_PTS, {"pts": value}
    else:
        action, params = pts.ACTION_SET_RANK, {"rank": rank_key}
    pts.admin_change(db, user, action, params, at)
    log(db, admin, user, action, {**params, "before": before, "after": _pts_state(user)}, at=at)
    db.commit()


def recalibrate(db: Session, admin: User, user: User) -> None:
    if user.calibrated_at is None:
        raise AdminError("Калибровки ещё нет: ранг появится после входного пробника")
    at = datetime.now(UTC)
    before = _pts_state(user)
    pts.admin_change(db, user, pts.ACTION_RECALIBRATE, {}, at)
    log(db, admin, user, pts.ACTION_RECALIBRATE, {"before": before}, at=at)
    db.commit()


@dataclass
class ResetSummary:
    attempts: int
    exams: int


def reset_stats(db: Session, r: Redis, admin: User, user: User) -> ResetSummary:
    """Удалить все ответы, пробники, PTS и достижения ученика. Профиль (ник, аватарка, роль) остаётся.

    После — полный пересчёт PTS: рейтинги задач тоже «забывают» ответы ученика.
    """
    solved = db.scalar(select(func.count(distinct(Attempt.task_id)))
                       .where(Attempt.user_id == user.id, Attempt.is_correct.is_(True)))
    attempts = db.scalar(select(func.count()).where(Attempt.user_id == user.id))
    exams = db.scalar(select(func.count()).where(ExamSession.user_id == user.id))
    log(db, admin, user, pts.ACTION_RESET_STATS,
        {"before": {**_pts_state(user), "solved": solved, "attempts": attempts, "exams": exams}})

    db.execute(delete(PtsChange).where(PtsChange.user_id == user.id))
    db.execute(delete(UserNumberRating).where(UserNumberRating.user_id == user.id))
    db.execute(delete(Attempt).where(Attempt.user_id == user.id))
    db.execute(delete(ExamSession).where(ExamSession.user_id == user.id))
    db.execute(delete(UserAchievement).where(UserAchievement.user_id == user.id))
    db.commit()
    r.delete(f"exam:active:{user.id}")

    pts.recompute_all(db)  # сам сбросит PTS ученика: событие stats.reset уже в журнале
    rating.rebuild(r, db)
    db.refresh(user)
    return ResetSummary(attempts, exams)
