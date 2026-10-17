"""Audit context: the audit log as a reader may see it, and audit activity counts.

`audit_entries` is what `GET /api/audit` returns (the router only translates HTTP). Readers
without `audit:read:identified` (Compliance) get account emails and patient / HCP ids masked by
`serializers.audit_out`, exactly as before. `audit_activity` counts events per action and day:
figures only, no actor, entity, reason or detail, so it cannot become a second route to what
the masking hides. Read only.
"""

from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock, pii
from app.core.permissions import Permission, can
from app.models import AuditLog, Patient, User
from app.services import serializers as out
from app.services.errors import Forbidden
from app.services.schemas import Contract

MAX_LIMIT = 500
MAX_DAYS = 90


class AuditPage(Contract):
    total: int
    actions: dict[str, int]
    items: list[dict]


class AuditActivityRow(Contract):
    action: str
    day: date
    count: int


def _require(user: User) -> None:
    if not can(user, Permission.AUDIT_READ):
        raise Forbidden("Your account cannot read the audit log.")


def audit_entries(
    db: Session,
    user: User,
    *,
    action: str | None = None,
    nba_id: int | None = None,
    actor: str | None = None,
    entity_id: str | None = None,
    mine: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> AuditPage:
    _require(user)
    identified = can(user, Permission.AUDIT_READ_IDENTIFIED)
    where = []
    if action:
        where.append(AuditLog.action == action)
    if nba_id is not None:
        where.append(AuditLog.nba_id == nba_id)
    if actor:
        # Filtering by a patient's account would confirm whose actions they are: that filter
        # never matches patient accounts.
        where.append(AuditLog.actor == actor)
        where.append(AuditLog.actor_role.not_in(pii.PATIENT_ROLES))
    if entity_id:
        where.append(AuditLog.entity_id == entity_id)
    if mine:
        where.append(AuditLog.actor_user_id == user.id)
    total = db.scalar(select(func.count()).select_from(AuditLog).where(*where))
    actions = dict(
        db.execute(select(AuditLog.action, func.count()).group_by(AuditLog.action)).all()
    )
    rows = db.scalars(
        select(AuditLog)
        .where(*where)
        .order_by(AuditLog.id.desc())
        .limit(max(1, min(limit, MAX_LIMIT)))
        .offset(max(0, offset))
    ).all()
    verified = set(
        db.scalars(
            select(User.username).where(
                User.username.in_({a.actor for a in rows}), User.professionally_verified
            )
        )
    )
    viewer = pii.PatientViewer(db, user)
    return AuditPage(
        total=total,
        actions=actions,
        items=[
            _masked(
                db,
                viewer,
                a,
                out.audit_out(
                    a, identified=identified, verified_actors=verified, viewer_id=user.id
                ),
            )
            for a in rows
        ],
    )


def _masked(db: Session, viewer: pii.PatientViewer, a: AuditLog, row: dict) -> dict:
    """Patient PII in an audit row, masked for a reader who may not see that patient
    (`core.pii`): a patient account's handle (its email) as the actor, and the patient's name
    inside the reason and detail."""
    account = None
    if a.actor_role in pii.PATIENT_ROLES:
        account = db.scalar(select(User).where(User.username == a.actor))
        if not (account and viewer.sees(account.patient_id)) and row.get("actor"):
            row["actor"] = pii.mask_email(row["actor"])
    patient_id = account.patient_id if account is not None else None
    if a.entity_type == "patient" and a.entity_id:
        patient_id = a.entity_id
    if patient_id and not viewer.sees(patient_id):
        p = db.get(Patient, patient_id)
        if p is not None:
            names = [p.first_name, p.last_name]
            if account is not None:
                names.append(account.display_name)
            for key in ("reason", "detail"):
                row[key] = pii.mask_tree(row.get(key), lambda s: pii.mask_in_text(s, names))
    return row


def audit_activity(
    db: Session, user: User, *, days: int = 14, actions: tuple[str, ...] = ()
) -> list[AuditActivityRow]:
    """Events per action and day over the last `days` days (at most `MAX_DAYS`), oldest first.
    Optional `actions` narrows to those action codes (for example the AI events)."""
    _require(user)
    days = max(1, min(days, MAX_DAYS))
    since = datetime.combine(clock.get_today(db) - timedelta(days=days - 1), datetime.min.time())
    day = func.date(AuditLog.ts)
    query = select(AuditLog.action, day, func.count()).where(AuditLog.ts >= since)
    if actions:
        query = query.where(AuditLog.action.in_(actions))
    rows = db.execute(query.group_by(AuditLog.action, day).order_by(day, AuditLog.action)).all()
    return [
        AuditActivityRow(action=a, day=date.fromisoformat(str(d)[:10]), count=n) for a, d, n in rows
    ]
