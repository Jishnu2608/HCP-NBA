"""Care-request context: the care manager's queue, one scoped care request, and request
timestamps for cycle-time figures.

The queue is what `GET /api/care/requests` returns (the router now only translates HTTP); the
single request and the timestamps are scoped like Patient 360, where the same requests appear
in the health record. Read only.
"""

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clinical import care, workload
from app.core import clock, rbac
from app.core.permissions import Permission, can, can_any
from app.models import CareRequest, User
from app.services.errors import Forbidden, NotFound
from app.services.schemas import Contract

PATIENT_READ = (Permission.PATIENT_READ_ALL, Permission.PATIENT_READ_ASSIGNED)
QUEUE_LIMIT = 300  # requests_for returns at most this many rows


class CareQueue(Contract):
    counts: dict[str, int]
    work: dict
    items: list[dict]
    total: int  # rows matching the filter (bounded by the domain query's limit)


class RequestTimes(Contract):
    """One request's workflow timestamps: no id, no patient, no text."""

    type: str
    status: str
    created_at: datetime
    routed_at: datetime | None
    responded_at: datetime | None
    closed_at: datetime | None
    due_date: date | None
    overdue: bool


def _scope(user: User):
    return lambda column: rbac.patient_filter(user, column)


def care_queue(
    db: Session, user: User, *, status: str | None = None, type_: str | None = None
) -> CareQueue:
    """The care requests of the reader's panel, most urgent first, with status counts and the
    workload figures the menu shows."""
    if not can(user, Permission.PATIENT_CARE_MANAGE):
        raise Forbidden("Your account cannot work care requests.")
    rows = care.requests_for(db, _scope(user), status, type_)
    every = care.requests_for(db, _scope(user), None, None)
    counts: dict[str, int] = {}
    for r in every:
        counts[r.status] = counts.get(r.status, 0) + 1
    return CareQueue(
        counts=counts,
        work=workload.care_requests(db, user),
        items=[care.request_out(db, r) for r in rows],
        total=len(rows),
    )


def scoped_request(db: Session, user: User, request_id: int) -> CareRequest:
    if not can_any(user, *PATIENT_READ):
        raise Forbidden("Your account cannot read patient records.")
    row = db.scalar(
        select(CareRequest).where(
            CareRequest.id == request_id, rbac.patient_filter(user, CareRequest.patient_id)
        )
    )
    if row is None:
        raise NotFound("Care request not found")
    return row


def care_request(db: Session, user: User, request_id: int) -> dict:
    """One care request as staff see it in the health record (`care.request_out`)."""
    return care.request_out(db, scoped_request(db, user, request_id))


def request_times(db: Session, user: User, *, limit: int = 500) -> list[RequestTimes]:
    """Workflow timestamps of the care requests in the reader's patient scope, newest first,
    at most `limit`. For cycle-time figures computed in code; carries no identity or text."""
    if not can_any(user, *PATIENT_READ):
        raise Forbidden("Your account cannot read patient records.")
    today = clock.get_today(db)
    rows = db.scalars(
        select(CareRequest)
        .where(rbac.patient_filter(user, CareRequest.patient_id))
        .order_by(CareRequest.created_at.desc(), CareRequest.id.desc())
        .limit(min(limit, 2000))
    )
    return [
        RequestTimes(
            type=r.type,
            status=r.status,
            created_at=r.created_at,
            routed_at=r.routed_at,
            responded_at=r.responded_at,
            closed_at=r.closed_at,
            due_date=r.due_date,
            overdue=r.due_date is not None and r.due_date <= today and r.status != "closed",
        )
        for r in rows
    ]
