"""Account administration: view accounts, change status, change assignments.

There is deliberately no endpoint that changes a role. Assignments (data scope) and status
are editable; permissions follow only from the role the account was created with.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.api.deps import not_found, require_permission
from app.auth.errors import AuthError
from app.auth.provisioning import HCPS, OWN_HCP, OWN_PATIENT, PATIENTS, assignments
from app.auth.sessions import sessions
from app.core.db import get_db
from app.core.permissions import Permission, permissions_for
from app.models import Hcp, Patient, User
from app.models.enums import AccountSource, AccountStatus

router = APIRouter(prefix="/api/admin/users", tags=["users"])

manager = require_permission(Permission.USER_MANAGE)


class StatusBody(BaseModel):
    status: Literal["active", "disabled"]


class AssignmentBody(BaseModel):
    patient_ids: list[str] | None = None
    hcp_ids: list[str] | None = None
    patient_id: str | None = None
    hcp_id: str | None = None


def _summary(db: Session, user: User) -> dict:
    return {
        "id": user.id,
        "name": user.display_name,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "verified": user.verified,
        "source": user.source,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "assignment": assignments.summary(db, user),
    }


def _detail(db: Session, user: User) -> dict:
    result = _summary(db, user)
    result["permissions"] = sorted(permissions_for(user.role))
    kind = result["assignment"]["kind"]
    if kind == PATIENTS:
        ids = assignments.patient_ids(db, user)
        rows = db.scalars(select(Patient).where(Patient.patient_id.in_(ids))) if ids else []
        result["patients"] = [
            {
                "patient_id": p.patient_id,
                "name": out.patient_name(p),
                "risk_segment": p.risk_segment,
            }
            for p in rows
        ]
    elif kind == HCPS:
        ids = assignments.hcp_ids(db, user)
        rows = db.scalars(select(Hcp).where(Hcp.hcp_id.in_(ids))) if ids else []
        result["hcps"] = [
            {"hcp_id": h.hcp_id, "name": out.hcp_name(h), "specialty": h.specialty} for h in rows
        ]
    elif kind == OWN_PATIENT and user.patient_id:
        p = db.get(Patient, user.patient_id)
        result["patient"] = {"patient_id": p.patient_id, "name": out.patient_name(p)}
    elif kind == OWN_HCP and user.hcp_id:
        h = db.get(Hcp, user.hcp_id)
        result["hcp"] = {"hcp_id": h.hcp_id, "name": out.hcp_name(h), "specialty": h.specialty}
    return result


def _get(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise not_found("Account not found")
    return user


@router.get("")
def list_users(
    role: str | None = None,
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    where = []
    if role:
        where.append(User.role == role)
    if status:
        where.append(User.status == status)
    if source:
        where.append(User.source == source)
    if q:
        like = f"%{q}%"
        where.append(or_(User.display_name.ilike(like), User.email.ilike(like)))
    total = db.scalar(select(func.count()).select_from(User).where(*where))
    by_role = dict(db.execute(select(User.role, func.count()).group_by(User.role)).all())
    # Registered accounts first, newest first: those are the ones an admin acts on.
    rows = db.scalars(
        select(User)
        .where(*where)
        .order_by((User.source == AccountSource.SIGNUP).desc(), User.created_at.desc(), User.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return {"total": total, "by_role": by_role, "items": [_summary(db, u) for u in rows]}


@router.get("/{user_id}")
def get_user(user_id: int, _: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    return _detail(db, _get(db, user_id))


@router.patch("/{user_id}/status")
def set_status(
    user_id: int, body: StatusBody, admin: User = Depends(manager), db: Session = Depends(get_db)
) -> dict:
    user = _get(db, user_id)
    if user.id == admin.id:
        raise AuthError(409, "cannot_change_self", "You cannot change your own account status.")
    if user.source == AccountSource.SYSTEM:
        raise AuthError(409, "system_account", "The system administrator cannot be disabled.")
    if body.status == AccountStatus.ACTIVE and not user.verified:
        raise AuthError(409, "not_verified", "This account has not verified its email yet.")
    previous = user.status
    user.status = body.status
    if body.status == AccountStatus.DISABLED:
        sessions.revoke(user)  # signs the account out everywhere, immediately
    audit.record(
        db,
        "account_status_changed",
        "user",
        user.id,
        actor=admin.username,
        actor_role=admin.role,
        detail={"account": user.email, "previous": previous, "new": body.status},
    )
    db.commit()
    return _detail(db, user)


@router.put("/{user_id}/assignments")
def set_assignments(
    user_id: int,
    body: AssignmentBody,
    admin: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    """Replaces what the account is assigned to. Role and permissions are not affected."""
    user = _get(db, user_id)
    before = assignments.summary(db, user)
    after = assignments.replace(
        db, user, **body.model_dump(), fields_set=set(body.model_fields_set)
    )
    audit.record(
        db,
        "assignments_changed",
        "user",
        user.id,
        actor=admin.username,
        actor_role=admin.role,
        detail={"account": user.email, "role": user.role, "before": before, "after": after},
    )
    db.commit()
    return _detail(db, user)
