from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.api.deps import not_found, require_permission
from app.core import clock
from app.core.db import get_db
from app.core.permissions import Permission, can
from app.models import Content, ContentReview, Nba, User
from app.models.enums import MlrStatus, NbaStatus, TargetType

router = APIRouter(prefix="/api/content", tags=["content"])

APPROVAL_VALID_DAYS = 730
# Without the read-all permission, an account only sees material cleared for use,
# for the audience its permission names.
APPROVED_AUDIENCE = {
    Permission.CONTENT_READ_APPROVED_HCP: TargetType.HCP,
    Permission.CONTENT_READ_APPROVED_PATIENT: TargetType.PATIENT,
}
readers = require_permission(Permission.CONTENT_READ_ALL, *APPROVED_AUDIENCE)


class ReviewBody(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = None


def _waiting_on(db: Session) -> dict[str, int]:
    """content_id -> number of open recommendations holding that content back."""
    counts: dict[str, int] = {}
    for withheld in db.scalars(
        select(Nba.withheld).where(
            Nba.withheld.is_not(None),
            Nba.status.in_((NbaStatus.READY_FOR_REVIEW, NbaStatus.BLOCKED)),
        )
    ):
        cid = withheld.get("content_id")
        if cid:
            counts[cid] = counts.get(cid, 0) + 1
    # Blocked outright because of the content itself (not because of missing consent).
    today = clock.get_today(db)
    for cid in db.scalars(select(Nba.content_id).where(Nba.status == NbaStatus.BLOCKED)):
        if not out.content_out(db.get(Content, cid), today)["usable"]:
            counts[cid] = counts.get(cid, 0) + 1
    return counts


@router.get("")
def list_content(user: User = Depends(readers), db: Session = Depends(get_db)) -> list[dict]:
    today = clock.get_today(db)
    rows = [
        out.content_out(c, today) for c in db.scalars(select(Content).order_by(Content.content_id))
    ]
    if not can(user, Permission.CONTENT_READ_ALL):
        audiences = {a for p, a in APPROVED_AUDIENCE.items() if can(user, p)}
        return [r for r in rows if r["usable"] and r["audience"] in audiences]
    waiting = _waiting_on(db)
    return [{**r, "recommendations_waiting": waiting.get(r["content_id"], 0)} for r in rows]


@router.post("/{content_id}/review")
def review_content(
    content_id: str,
    body: ReviewBody,
    user: User = Depends(require_permission(Permission.CONTENT_APPROVE)),
    db: Session = Depends(get_db),
) -> dict:
    """MLR decision. Requires the content-approval permission, which only Compliance holds."""
    content = db.get(Content, content_id)
    if content is None:
        raise not_found("Content not found")
    today = clock.get_today(db)
    if content.mlr_status == MlrStatus.REJECTED and body.decision == "approve":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Rejected content must be revised and resubmitted"
        )
    previous = content.mlr_status
    if body.decision == "approve":
        content.mlr_status = MlrStatus.APPROVED
        content.effective_date = today
        content.expiry_date = today + timedelta(days=APPROVAL_VALID_DAYS)
    else:
        content.mlr_status = MlrStatus.REJECTED
        content.effective_date = content.expiry_date = None
    db.add(
        ContentReview(
            content_id=content_id,
            reviewer_user_id=user.id,
            decision=body.decision,
            comment=body.comment,
        )
    )
    waiting = _waiting_on(db).get(content_id, 0)
    audit.record(
        db,
        "content_approved" if body.decision == "approve" else "content_rejected",
        "content",
        content_id,
        actor=user.username,
        actor_role=user.role,
        compliance_ok=body.decision == "approve",
        reason=body.comment,
        detail={"previous_status": previous, "recommendations_waiting": waiting},
    )
    db.commit()
    return {**out.content_out(content, today), "recommendations_waiting": waiting}
