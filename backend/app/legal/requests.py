"""Privacy requests (data-subject requests).

A request is a ticket: submitting it records what was asked and by whom, and a person with
`privacy:manage` works it and records the outcome. Nothing is changed or deleted by
submitting. The one request answered automatically is a copy of your own data
(`export`), which is a download, not a ticket.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.errors import AuthError
from app.core import jurisdiction
from app.core.config import get_settings
from app.core.permissions import Permission, can
from app.models import PrivacyRequest, User
from app.models.tables import utcnow

TYPES: dict[str, str] = {
    "access": "Access my personal data",
    "rectification": "Correct my personal data",
    "erasure": "Delete my personal data / account",
    "restriction": "Restrict processing of my data",
    "portability": "Receive my data in a portable format",
    "objection": "Object to processing of my data",
    "consent_withdrawal": "Withdraw a consent",
    "other": "Other privacy question",
}
STATUSES = ("submitted", "in_review", "completed", "rejected")
OPEN = ("submitted", "in_review")


def create_request(db: Session, user: User, type_: str, details: str | None) -> PrivacyRequest:
    if type_ not in TYPES:
        raise AuthError(422, "invalid_request_type", "Choose a request type.")
    days = get_settings().privacy_response_days
    now = utcnow()
    row = PrivacyRequest(
        user_id=user.id,
        type=type_,
        status="submitted",
        details=(details or "").strip()[:2000] or None,
        jurisdiction=jurisdiction.code(user.country, user.region),
        created_at=now,
        updated_at=now,
        respond_by=(now + timedelta(days=days)).date() if days else None,
    )
    db.add(row)
    db.flush()
    # The request text can hold personal details: it is not copied into the audit log.
    audit.record(
        db, "privacy_request_submitted", "privacy_request", row.id, actor=user.username,
        actor_role=user.role, detail={"type": type_},
    )  # fmt: skip
    return row


def out(row: PrivacyRequest, *, with_requester: User | None = None) -> dict:
    data = {
        "id": row.id,
        "type": row.type,
        "type_label": TYPES.get(row.type, row.type),
        "status": row.status,
        "details": row.details,
        "resolution": row.resolution,
        "jurisdiction": row.jurisdiction,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "respond_by": row.respond_by,
        # Set once the requester's account was deleted (the request is kept as the record).
        "subject_label": row.subject_label,
    }
    if with_requester is not None:
        data["requester"] = {
            "id": with_requester.id,
            "name": with_requester.display_name,
            "email": with_requester.email,
            "role": with_requester.role,
            # Display hint: a patient account can be deleted from this request.
            "deletable": can(with_requester, Permission.SELF_HEALTH_MANAGE),
        }
    return data


def own(db: Session, user: User) -> list[dict]:
    rows = db.scalars(
        select(PrivacyRequest)
        .where(PrivacyRequest.user_id == user.id)
        .order_by(PrivacyRequest.id.desc())
    )
    return [out(r) for r in rows]


def update(db: Session, handler: User, request_id: int, status: str, resolution: str | None):
    row = db.get(PrivacyRequest, request_id)
    if row is None:
        raise AuthError(404, "request_not_found", "Request not found.")
    if status not in STATUSES:
        raise AuthError(422, "invalid_status", "Choose a valid status.")
    if row.status not in OPEN:
        raise AuthError(409, "request_closed", "This request is already closed.")
    if status in ("completed", "rejected") and not (resolution or "").strip():
        raise AuthError(422, "resolution_required", "Record what was done before closing.")
    previous = row.status
    row.status = status
    row.resolution = (resolution or "").strip()[:2000] or row.resolution
    row.handled_by_user_id = handler.id
    row.updated_at = utcnow()
    audit.record(
        db, "privacy_request_updated", "privacy_request", row.id, actor=handler.username,
        actor_role=handler.role, detail={"previous": previous, "new": status},
    )  # fmt: skip
    db.flush()
    return row
