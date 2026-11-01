"""Админка: ученики и журнал. Всё — только для администраторов (любой админ может всё).

GET   /admin/users                      — список: ?q=поиск&filter=all|admins|blocked|no_rank&sort=new|recent|pts|solved|name
GET   /admin/users/{id}                 — карточка ученика (+ последние действия админов с ним)
PATCH /admin/users/{id}                 — {nickname, created_at, role, blocked}: меняются присланные поля
POST  /admin/users/{id}/pts             — {pts: 1500} или {rank: "hero"}
POST  /admin/users/{id}/recalibrate     — сбросить калибровку: следующий пробник снова входной
POST  /admin/users/{id}/reset-stats     — удалить всю статистику (ответы, пробники, PTS, достижения)
DELETE /admin/users/{id}/avatar         — сбросить аватарку на фото из Telegram
GET   /admin/actions                    — журнал: ?user_id=&admin_id=&before=<id>&limit=
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from redis import Redis
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.redis import get_redis
from app.modules.admin import service
from app.modules.admin.schemas import ActionList, AdminUserDetail, AdminUserList, PtsChange, UserPatch
from app.modules.auth import avatars
from app.modules.auth.deps import require_admin
from app.modules.auth.models import User

router = APIRouter(prefix="/admin", tags=["Админка"])


def _target(db: Session, user_id: int) -> User:
    try:
        return service.get_target(db, user_id)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден") from None


def _bad(error: service.AdminError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


@router.get("/users", response_model=AdminUserList)
def list_users(
    q: str = Query("", max_length=100),
    filter: str = Query("all", pattern="^(all|admins|blocked|no_rank)$"),
    sort: str = Query("new", pattern="^(new|recent|pts|solved|name)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return service.list_users(db, q, filter, sort, limit, offset)


@router.get("/users/{user_id}", response_model=AdminUserDetail)
def get_user(user_id: int, db: Session = Depends(get_db), _admin: User = Depends(require_admin)):
    return service.detail(db, _target(db, user_id))


@router.patch("/users/{user_id}", response_model=AdminUserDetail)
def patch_user(
    user_id: int,
    body: UserPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = _target(db, user_id)
    fields = body.model_fields_set
    try:
        if "nickname" in fields:
            service.set_nickname(db, admin, user, body.nickname)
        if "created_at" in fields:
            if body.created_at is None:
                raise service.AdminError("Укажите дату")
            service.set_created_at(db, admin, user, body.created_at)
        if "role" in fields and body.role is not None:
            service.set_role(db, admin, user, body.role)
        if "blocked" in fields and body.blocked is not None:
            service.set_blocked(db, admin, user, body.blocked)
    except service.AdminError as error:
        db.rollback()
        raise _bad(error) from None
    db.commit()
    return service.detail(db, user)


@router.post("/users/{user_id}/pts", response_model=AdminUserDetail)
def change_pts(user_id: int, body: PtsChange, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = _target(db, user_id)
    try:
        service.change_pts(db, admin, user, body.pts, body.rank)
    except service.AdminError as error:
        db.rollback()
        raise _bad(error) from None
    return service.detail(db, user)


@router.post("/users/{user_id}/recalibrate", response_model=AdminUserDetail)
def recalibrate(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    user = _target(db, user_id)
    try:
        service.recalibrate(db, admin, user)
    except service.AdminError as error:
        raise _bad(error) from None
    return service.detail(db, user)


@router.post("/users/{user_id}/reset-stats", response_model=AdminUserDetail)
def reset_stats(
    user_id: int, db: Session = Depends(get_db), r: Redis = Depends(get_redis), admin: User = Depends(require_admin)
):
    user = _target(db, user_id)
    service.reset_stats(db, r, admin, user)
    return service.detail(db, user)


@router.delete("/users/{user_id}/avatar", response_model=AdminUserDetail)
def reset_avatar(
    user_id: int, background: BackgroundTasks, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    user = _target(db, user_id)
    service.reset_avatar(db, admin, user)
    background.add_task(avatars.refresh_from_telegram, user.id)
    return service.detail(db, user)


@router.get("/actions", response_model=ActionList)
def list_actions(
    user_id: int | None = None,
    admin_id: int | None = None,
    before: int | None = None,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return service.actions(db, user_id=user_id, admin_id=admin_id, before=before, limit=limit)
