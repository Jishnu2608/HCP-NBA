"""An HCP's consultation figures: what is waiting for them, how long answers took, and
their answered-in-a-row streak. Only consultations routed to this HCP (or that they
declined) are read. Speed is shown as information, never as a score."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.insights import streaks, typical
from app.models import CareNote, CareRequest
from app.models.enums import CareNoteKind, CareRequestStatus, CareRequestType

# Text the system writes on a consultation it returned by itself (a specialty change): not
# the HCP's decision, so it counts neither for nor against them.
AUTOMATIC_RETURN = "Returned automatically"


def _declines(db: Session, hcp_id: str) -> list[CareNote]:
    return [
        n
        for n in db.scalars(
            select(CareNote).where(
                CareNote.hcp_id == hcp_id, CareNote.kind == CareNoteKind.HCP_DECLINE
            )
        )
        if not (n.text or "").startswith(AUTOMATIC_RETURN)
    ]


def consultation_workload(db: Session, hcp_id: str) -> dict:
    """Where the consultations routed to this HCP are now. The keys match the groups of the
    HCP's consultation list, so a segment can lead to its group."""
    rows = db.scalars(
        select(CareRequest).where(
            CareRequest.type == CareRequestType.CONSULTATION, CareRequest.assigned_hcp_id == hcp_id
        )
    ).all()
    waiting = sum(1 for r in rows if r.status == CareRequestStatus.AWAITING_HCP)
    with_cm = sum(1 for r in rows if r.status == CareRequestStatus.HCP_RESPONDED)
    closed = sum(
        1 for r in rows if r.status == CareRequestStatus.CLOSED and r.responded_at is not None
    )
    current = {r.id for r in rows}
    declined = len({n.request_id for n in _declines(db, hcp_id) if n.request_id not in current})
    segments = [
        {"key": "waiting", "label": "Waiting for your answer", "value": waiting, "tone": "warn"},
        {
            "key": "with_care_manager",
            "label": "Answered, care manager closing",
            "value": with_cm,
            "tone": "info",
        },
        {"key": "history", "label": "Answered and closed", "value": closed, "tone": "ok"},
        {"key": "history", "label": "Declined by you", "value": declined, "tone": "neutral"},
    ]
    return {"total": sum(s["value"] for s in segments), "segments": segments}


def response_times(db: Session, hcp_id: str) -> dict:
    """Hours from routing to this HCP's answer, per answered consultation, with the median
    once there are enough answers."""
    rows = db.scalars(
        select(CareRequest)
        .where(
            CareRequest.type == CareRequestType.CONSULTATION,
            CareRequest.assigned_hcp_id == hcp_id,
            CareRequest.responded_at.is_not(None),
            CareRequest.routed_at.is_not(None),
        )
        .order_by(CareRequest.responded_at)
    ).all()
    items = [
        {
            "request_id": r.id,
            "routed_at": r.routed_at,
            "responded_at": r.responded_at,
            "hours": round(max(0.0, (r.responded_at - r.routed_at).total_seconds() / 3600), 1),
        }
        for r in rows
        if r.responded_at >= r.routed_at
    ]
    return {"items": items, "summary": typical([i["hours"] for i in items])}


def answered_streak(db: Session, hcp_id: str) -> dict | None:
    outcomes: list[tuple[datetime, bool]] = [
        (r.responded_at, True)
        for r in db.scalars(
            select(CareRequest).where(
                CareRequest.type == CareRequestType.CONSULTATION,
                CareRequest.assigned_hcp_id == hcp_id,
                CareRequest.responded_at.is_not(None),
            )
        )
    ]
    outcomes += [(n.created_at, False) for n in _declines(db, hcp_id)]
    return streaks.answered_in_a_row(outcomes)
