from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.api.deps import not_found, require_permission
from app.api.schemas import StrictBody
from app.core import clock, rbac
from app.core.db import get_db
from app.core.permissions import Permission
from app.engagement import delivery
from app.llm import service as drafting
from app.llm.base import DraftOutput, MessageVariant
from app.llm.validator import DraftValidationError, validate
from app.models import Interaction, MessageDraft, Nba, Patient, User
from app.models.enums import Channel, NbaStatus, Outcome, PatientOrigin, TargetType
from app.models.tables import utcnow
from app.nba import gates
from app.nba.revalidate import current_failures

router = APIRouter(prefix="/api/nba", tags=["recommendations"])

OPEN = (NbaStatus.READY_FOR_REVIEW, NbaStatus.APPROVED, NbaStatus.BLOCKED)
REAL_PATIENTS = select(Patient.patient_id).where(Patient.origin != PatientOrigin.SYNTHETIC)

# Any recommendation endpoint needs a recommendation-read permission; which rows are
# visible is then decided by rbac.nba_filter, and acting on one by rbac.can_review.
readers = require_permission(
    Permission.NBA_READ_ALL,
    Permission.NBA_READ_GATED,
    Permission.NBA_READ_HCP_ASSIGNED,
    Permission.NBA_READ_PATIENT_ASSIGNED,
)


class ApproveBody(StrictBody):
    draft_id: int | None = None


class RejectBody(StrictBody):
    reason: str = Field(min_length=3, max_length=500)


class OutcomeBody(StrictBody):
    outcome: Literal["completed", "declined", "no_response"]
    note: str | None = Field(default=None, max_length=500)


class DraftEdit(StrictBody):
    subject: str | None = Field(default=None, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


def _scoped(db: Session, user: User, nba_id: int) -> Nba:
    nba = db.scalar(select(Nba).where(Nba.id == nba_id, rbac.nba_filter(user)))
    if nba is None:
        raise not_found("Recommendation not found")
    return nba


def _reviewable(db: Session, user: User, nba_id: int) -> Nba:
    nba = _scoped(db, user, nba_id)
    if not rbac.can_review(user, nba):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            {"code": "forbidden", "message": "Your account cannot review this recommendation."},
        )
    return nba


def _require_status(nba: Nba, *allowed: str) -> None:
    if nba.status not in allowed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Recommendation is {nba.status}, expected {' or '.join(allowed)}",
        )


@router.get("")
def list_recommendations(
    status_in: list[str] | None = Query(None, alias="status"),
    target_type: str | None = None,
    real_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(readers),
    db: Session = Depends(get_db),
) -> dict:
    """The work queue: recommendations this user may see, highest priority first."""
    where = [rbac.nba_filter(user), Nba.status.in_(status_in or OPEN)]
    if target_type:
        where.append(Nba.target_type == target_type)
    # Real people (self-registered and clinic patients), as opposed to the demo population.
    real = and_(Nba.target_type == TargetType.PATIENT, Nba.target_id.in_(REAL_PATIENTS))
    if real_only:
        where.append(real)
    total = db.scalar(select(func.count()).select_from(Nba).where(*where))
    real_waiting = db.scalar(
        select(func.count())
        .select_from(Nba)
        .where(rbac.nba_filter(user), real, Nba.status == NbaStatus.READY_FOR_REVIEW)
    )
    by_status = dict(
        db.execute(
            select(Nba.status, func.count())
            .where(rbac.nba_filter(user), Nba.status != NbaStatus.EXPIRED)
            .group_by(Nba.status)
        ).all()
    )
    rows = db.scalars(
        select(Nba).where(*where).order_by(Nba.priority.desc(), Nba.id).limit(limit).offset(offset)
    ).all()
    identity = rbac.can_see_target_profile(user)
    return {
        "total": total,
        "counts": by_status,
        "real_waiting": real_waiting if rbac.can_see_target_profile(user) else None,
        "items": [out.nba_summary(db, n, with_identity=identity) for n in rows],
    }


@router.get("/{nba_id}")
def get_recommendation(
    nba_id: int, user: User = Depends(readers), db: Session = Depends(get_db)
) -> dict:
    nba = _scoped(db, user, nba_id)
    detail = out.nba_detail(
        db, nba, clock.get_today(db), with_identity=rbac.can_see_target_profile(user)
    )
    detail["can_review"] = rbac.can_review(user, nba)
    return detail


@router.post("/{nba_id}/approve")
def approve(
    nba_id: int,
    body: ApproveBody,
    user: User = Depends(readers),
    db: Session = Depends(get_db),
) -> dict:
    nba = _reviewable(db, user, nba_id)
    _require_status(nba, NbaStatus.READY_FOR_REVIEW)

    # Gates are checked again now: approval or consent may have changed since generation.
    failures = current_failures(db, nba, include_frequency=False)
    if failures:
        nba.status, nba.block_reason = NbaStatus.BLOCKED, gates.describe(failures)
        audit.record(
            db,
            "nba_blocked_at_review",
            "nba",
            nba.id,
            nba_id=nba.id,
            actor=user.username,
            actor_role=user.role,
            compliance_ok=gates.compliance_ok(failures),
            consent_ok=gates.consent_ok(failures),
            reason=nba.block_reason,
            detail={"gate_failures": failures},
        )
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, f"No longer eligible: {nba.block_reason}")

    drafts = db.scalars(select(MessageDraft).where(MessageDraft.nba_id == nba.id)).all()
    if body.draft_id is not None:
        if body.draft_id not in {d.id for d in drafts}:
            raise not_found("Draft not found")
        for d in drafts:
            d.is_selected = d.id == body.draft_id
    nba.status = NbaStatus.APPROVED
    nba.reviewed_by_user_id, nba.reviewed_ts = user.id, utcnow()
    audit.record(
        db,
        "nba_approved",
        "nba",
        nba.id,
        nba_id=nba.id,
        actor=user.username,
        actor_role=user.role,
        compliance_ok=True,
        consent_ok=True if nba.target_type == "PATIENT" else None,
        reason="Approved after gate re-check",
    )
    db.commit()
    return out.nba_detail(db, nba, clock.get_today(db))


@router.post("/{nba_id}/reject")
def reject(
    nba_id: int,
    body: RejectBody,
    user: User = Depends(readers),
    db: Session = Depends(get_db),
) -> dict:
    nba = _reviewable(db, user, nba_id)
    _require_status(nba, NbaStatus.READY_FOR_REVIEW, NbaStatus.APPROVED)
    nba.status = NbaStatus.REJECTED
    nba.reviewed_by_user_id, nba.reviewed_ts = user.id, utcnow()
    audit.record(
        db,
        "nba_rejected",
        "nba",
        nba.id,
        nba_id=nba.id,
        actor=user.username,
        actor_role=user.role,
        reason=body.reason,
    )
    db.commit()
    return out.nba_detail(db, nba, clock.get_today(db))


@router.patch("/{nba_id}/drafts/{draft_id}")
def edit_draft(
    nba_id: int,
    draft_id: int,
    body: DraftEdit,
    user: User = Depends(readers),
    db: Session = Depends(get_db),
) -> dict:
    """A reviewer's edit goes through the same wording checks as model output."""
    nba = _reviewable(db, user, nba_id)
    _require_status(nba, NbaStatus.READY_FOR_REVIEW)
    draft = db.get(MessageDraft, draft_id)
    if draft is None or draft.nba_id != nba.id:
        raise not_found("Draft not found")
    edited = DraftOutput(
        rationale_summary=nba.rationale_summary or "Edited by reviewer.",
        variants=[MessageVariant(subject=body.subject, body=body.body)],
    )
    try:
        validate(edited, drafting.build_request(db, nba))
    except DraftValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.problems) from exc

    for d in db.scalars(select(MessageDraft).where(MessageDraft.nba_id == nba.id)):
        d.is_selected = d.id == draft.id
    draft.subject, draft.body, draft.edited_by_user_id = body.subject, body.body, user.id
    audit.record(
        db,
        "draft_edited",
        "nba",
        nba.id,
        nba_id=nba.id,
        actor=user.username,
        actor_role=user.role,
        detail={"draft_id": draft.id},
    )
    db.commit()
    return out.draft_out(draft)


@router.post("/{nba_id}/redraft")
def redraft(
    nba_id: int, user: User = Depends(readers), db: Session = Depends(get_db)
) -> list[dict]:
    """Ask the configured model provider for fresh wording. Falls back to templates on failure."""
    nba = _reviewable(db, user, nba_id)
    _require_status(nba, NbaStatus.READY_FOR_REVIEW)
    drafting.draft_for_nba(db, nba, actor=user.username, actor_role=user.role)
    db.commit()
    rows = db.scalars(
        select(MessageDraft).where(MessageDraft.nba_id == nba.id).order_by(MessageDraft.variant_no)
    )
    return [out.draft_out(d) for d in rows]


@router.post("/{nba_id}/send")
def send(nba_id: int, user: User = Depends(readers), db: Session = Depends(get_db)) -> dict:
    """Deliver an approved recommendation. Every gate is checked once more at this moment."""
    nba = _reviewable(db, user, nba_id)
    _require_status(nba, NbaStatus.APPROVED)
    try:
        delivery.send(db, nba, user)
    except delivery.SendBlocked as exc:
        db.commit()
        raise HTTPException(status.HTTP_409_CONFLICT, f"Not sent: {exc.reason}") from exc
    db.commit()
    return out.nba_detail(db, nba, clock.get_today(db))


@router.post("/{nba_id}/outcome")
def log_outcome(
    nba_id: int,
    body: OutcomeBody,
    user: User = Depends(readers),
    db: Session = Depends(get_db),
) -> dict:
    """Staff feedback for human channels: how the call or visit went."""
    nba = _reviewable(db, user, nba_id)
    _require_status(nba, NbaStatus.SENT)
    if nba.channel not in (Channel.PHONE, Channel.REP_VISIT):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Outcomes are logged by staff only for calls and visits"
        )
    interaction = db.scalar(
        select(Interaction).where(
            Interaction.nba_id == nba.id, Interaction.outcome == Outcome.PENDING
        )
    )
    if interaction is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Outcome already recorded")
    delivery.capture_response(
        db,
        interaction,
        body.outcome,
        delivery.now_on(db),
        actor=user.username,
        actor_role=user.role,
        note=body.note,
    )
    db.commit()
    return out.nba_detail(db, nba, clock.get_today(db))
