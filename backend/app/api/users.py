"""Account administration: view accounts, change status, change assignments, and
permanently delete patient accounts.

There is deliberately no endpoint that changes a role. Assignments (data scope) and status
are editable; permissions follow only from the role the account was created with.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.api.deps import not_found, require_permission
from app.api.schemas import StrictBody
from app.auth import erasure, invitations
from app.auth.errors import AuthError
from app.auth.provisioning import HCPS, OWN_HCP, OWN_PATIENT, PATIENTS, assignments
from app.auth.sessions import sessions
from app.clinical import activity, care, records
from app.clinical import hcps as hcp_records
from app.core import age, jurisdiction
from app.core.db import get_db
from app.core.permissions import Permission, can, permissions_for
from app.models import CareRequest, Hcp, Patient, SpecialtyChangeRequest, User
from app.models.enums import AccountSource, AccountStatus, CareRequestStatus

router = APIRouter(prefix="/api/admin/users", tags=["users"])

manager = require_permission(Permission.USER_MANAGE)
deleter = require_permission(Permission.USER_DELETE)


class StatusBody(StrictBody):
    status: Literal["active", "disabled"]


class DeleteBody(StrictBody):
    """The account's email, typed by the administrator to confirm, and optionally the
    erasure request this deletion carries out."""

    confirm_email: str = Field(max_length=254)
    privacy_request_id: int | None = None


class SpecialtiesBody(StrictBody):
    specialties: list[Annotated[str, Field(max_length=64)]] = Field(max_length=10)


class DecisionBody(StrictBody):
    notes: str | None = Field(default=None, max_length=500)


class AssignmentBody(StrictBody):
    patient_ids: list[Annotated[str, Field(max_length=16)]] | None = Field(None, max_length=500)
    hcp_ids: list[Annotated[str, Field(max_length=16)]] | None = Field(None, max_length=500)
    patient_id: str | None = Field(None, max_length=16)
    hcp_id: str | None = Field(None, max_length=16)


def _summary(db: Session, user: User) -> dict:
    inviter = db.get(User, user.invited_by_user_id) if user.invited_by_user_id else None
    return {
        "id": user.id,
        "name": user.display_name,
        "email": user.email,
        "role": user.role,
        "status": user.status,
        "verified": user.verified,
        "email_verified": user.verified,
        "professionally_verified": user.professionally_verified,
        "verification_source": user.verification_source,
        # Never the date of birth itself: only whether the holder is a minor.
        "age_band": age.age_band(user),
        # Country of residence as the person gave it (never a demo record's city).
        "location": jurisdiction.label(user.country, user.region),
        "country": user.country,
        "invited_by": invitations.person(inviter),
        "source": user.source,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "assignment": assignments.summary(db, user),
    }


def _lineage(db: Session, user: User) -> list[dict]:
    """Who brought this account in, back to the administrator or the platform."""
    chain, seen, current = [], set(), user
    while current is not None and current.id not in seen:
        seen.add(current.id)
        chain.append(invitations.person(current))
        current = db.get(User, current.invited_by_user_id) if current.invited_by_user_id else None
    return list(reversed(chain))


def _detail(db: Session, user: User) -> dict:
    result = _summary(db, user)
    result["lineage"] = _lineage(db, user)
    result["professionally_verified_at"] = user.professionally_verified_at
    result["permissions"] = sorted(permissions_for(user.role))
    kind = result["assignment"]["kind"]
    if kind == PATIENTS:
        ids = assignments.patient_ids(db, user)
        rows = (
            db.scalars(
                select(Patient)
                .where(Patient.patient_id.in_(ids))
                .order_by(*activity.patient_order())
            )
            if ids
            else []
        )
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
            {
                "hcp_id": h.hcp_id,
                "name": out.hcp_name(h),
                "specialties": hcp_records.specialty_out(db, h.hcp_id),
            }
            for h in rows
        ]
    elif kind == OWN_PATIENT and user.patient_id:
        p = db.get(Patient, user.patient_id)
        result["patient"] = {"patient_id": p.patient_id, "name": out.patient_name(p)}
    elif kind == OWN_HCP and user.hcp_id:
        h = db.get(Hcp, user.hcp_id)
        result["hcp"] = {
            "hcp_id": h.hcp_id,
            "name": out.hcp_name(h),
            "origin": h.origin,
            "specialties": hcp_records.specialty_out(db, h.hcp_id),
            "pending_request": hcp_records.pending_for(db, h.hcp_id),
        }
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
        .order_by(
            User.source.in_((AccountSource.SIGNUP, AccountSource.INVITATION)).desc(),
            User.created_at.desc(),
            User.id,
        )
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
    moved = returned = 0
    if body.status == AccountStatus.DISABLED and can(user, Permission.PATIENT_CARE_MANAGE):
        # A care manager's real patients are never left without one: refuse when nobody
        # could take them, otherwise move them (with their open work) before disabling.
        impact = records.transfer_impact(db, user)
        if impact["real_patients"] and impact["to"] is None:
            raise AuthError(
                409,
                "no_other_care_manager",
                "No other active care manager could take over this care manager's patients.",
            )
    user.status = body.status
    revoked = 0
    if body.status == AccountStatus.DISABLED:
        sessions.revoke_all(db, user)  # signs the account out everywhere, immediately
        # Invitations it sent can no longer be accepted: its authority has ended.
        revoked = invitations.revoke_from(db, user, admin)
        if can(user, Permission.PATIENT_CARE_MANAGE):
            moved = records.transfer_patients(db, user, admin)
        if user.hcp_id:
            # Consultations waiting for this HCP go back to their care managers.
            returned = care.return_consultations(db, admin, user.hcp_id)
    elif can(user, Permission.PATIENT_CARE_MANAGE):
        records.adopt_unassigned(db, admin.username, admin.role)
    audit.record(
        db,
        "account_status_changed",
        "user",
        user.id,
        actor=admin.username,
        actor_role=admin.role,
        detail={
            "account": user.email,
            "previous": previous,
            "new": body.status,
            "invitations_revoked": revoked,
            "patients_moved": moved,
            "consultations_returned": returned,
        },
    )
    db.commit()
    return _detail(db, user)


@router.get("/{user_id}/impact")
def disable_impact(user_id: int, _: User = Depends(manager), db: Session = Depends(get_db)) -> dict:
    """What disabling this account would change, shown before the administrator confirms."""
    user = _get(db, user_id)
    result: dict = {"real_patients": 0, "open_requests": 0, "to": None, "consultations": 0}
    if can(user, Permission.PATIENT_CARE_MANAGE):
        result |= records.transfer_impact(db, user)
    if user.hcp_id:
        result["consultations"] = db.scalar(
            select(func.count())
            .select_from(CareRequest)
            .where(
                CareRequest.assigned_hcp_id == user.hcp_id,
                CareRequest.status == CareRequestStatus.AWAITING_HCP,
            )
        )
    return result


@router.get("/attention/unassigned")
def unassigned_patients(_: User = Depends(manager), db: Session = Depends(get_db)) -> list[dict]:
    """Real patients waiting for a care manager (none active when they registered)."""
    return [
        {"patient_id": pid, "name": out.patient_name(db.get(Patient, pid))}
        for pid in records.unassigned_real_patients(db)
    ]


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
        db, user, **body.model_dump(), fields_set=set(body.model_fields_set), actor=admin
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


@router.delete("/{user_id}")
def delete_account(
    user_id: int, body: DeleteBody, admin: User = Depends(deleter), db: Session = Depends(get_db)
) -> dict:
    """Permanently deletes a patient account and the patient's own data (auth/erasure.py).
    Professional accounts are disabled instead, never deleted."""
    user = _get(db, user_id)
    result = erasure.erase_patient(db, admin, user, body.confirm_email, body.privacy_request_id)
    db.commit()
    return result


@router.put("/{user_id}/specialties")
def set_specialties(
    user_id: int,
    body: SpecialtiesBody,
    admin: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    """An administrator sets an HCP's specialties (0..n, controlled list). The HCP cannot."""
    user = _get(db, user_id)
    hcp = db.get(Hcp, user.hcp_id) if user.hcp_id else None
    if hcp is None or not can(user, Permission.SELF_SPECIALTY_REQUEST):
        raise AuthError(409, "not_an_hcp", "Specialties apply to HCP accounts only.")
    if hcp_records.pending_for(db, hcp.hcp_id):
        raise AuthError(409, "request_pending", "Decide the HCP's pending change request first.")
    hcp_records.set_specialties(db, admin, hcp, body.specialties, why="administrator")
    db.commit()
    return _detail(db, user)


# --- HCP specialty change requests (part of the administrator's request centre) ---------

requests_router = APIRouter(prefix="/api/admin/specialty-requests", tags=["users"])


@requests_router.get("")
def list_specialty_requests(
    status: Literal["pending", "approved", "rejected"] | None = None,
    _: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    query = select(SpecialtyChangeRequest).order_by(SpecialtyChangeRequest.id.desc())
    if status:
        query = query.where(SpecialtyChangeRequest.status == status)
    counts = dict(
        db.execute(
            select(SpecialtyChangeRequest.status, func.count()).group_by(
                SpecialtyChangeRequest.status
            )
        ).all()
    )
    return {
        "counts": counts,
        "items": [hcp_records.request_out(db, r, staff=True) for r in db.scalars(query)],
    }


@requests_router.post("/{request_id}/{decision}")
def decide_specialty_request(
    request_id: int,
    decision: Literal["approve", "reject"],
    body: DecisionBody,
    admin: User = Depends(manager),
    db: Session = Depends(get_db),
) -> dict:
    """Only an approval changes the HCP's specialties, and only if they are unchanged since
    the request was made."""
    row = hcp_records.decide(db, admin, request_id, decision == "approve", body.notes)
    db.commit()
    return hcp_records.request_out(db, row, staff=True)
