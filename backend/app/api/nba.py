from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import Field
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import audit
from app.api import serializers as out
from app.api.deps import not_found, require_permission
from app.api.schemas import StrictBody
from app.clinical import workload
from app.core import clock, rbac
from app.core.db import get_db
from app.core.permissions import Permission, can
from app.engagement import delivery
from app.llm import service as drafting
from app.models import Content, Interaction, MessageDraft, Nba, Patient, User
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
    # What the representative learned (interested, needs information, ...): kept with the
    # interaction for the next action and the engine.
    reason: Literal["interested", "need_info", "another_meeting", "follow_up", "not_now"] | None = (
        None
    )
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


STATUS_WORDS = {
    NbaStatus.READY_FOR_REVIEW: "waiting for review",
    NbaStatus.APPROVED: "approved and not yet sent",
    NbaStatus.SENT: "already sent",
    NbaStatus.RESPONDED: "already answered by the person",
    NbaStatus.REJECTED: "rejected",
    NbaStatus.BLOCKED: "blocked by a safeguard",
    NbaStatus.EXPIRED: "replaced by a newer recommendation",
}


def _conflict(code: str, message: str, **extra) -> HTTPException:
    """Errors in the application's shape: a code the page can act on and a plain message."""
    return HTTPException(status.HTTP_409_CONFLICT, {"code": code, "message": message, **extra})


def _require_status(nba: Nba, *allowed: str) -> None:
    if nba.status not in allowed:
        words = STATUS_WORDS.get(nba.status, nba.status)
        raise _conflict(
            "invalid_state",
            f"This recommendation is {words}, so this action is no longer possible.",
            status=nba.status,
        )


@router.get("")
def list_recommendations(
    status_in: list[str] | None = Query(None, alias="status"),
    target_type: str | None = None,
    real_only: bool = False,
    responses: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(readers),
    db: Session = Depends(get_db),
) -> dict:
    """The work queue: recommendations this user may see, highest priority first."""
    statuses = status_in or ([NbaStatus.RESPONDED] if responses else OPEN)
    where = [rbac.nba_filter(user), Nba.status.in_(statuses)]
    if target_type:
        where.append(Nba.target_type == target_type)
    # Real people (self-registered and clinic patients), as opposed to the demo population.
    real = and_(Nba.target_type == TargetType.PATIENT, Nba.target_id.in_(REAL_PATIENTS))
    if real_only:
        where.append(real)
    if responses:
        # Real patients' responses nobody on the care team has looked at yet.
        where.append(workload.responses_filter())
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
    if can(user, Permission.HCP_READ_ASSIGNED):
        # A representative's "sent" and "responded" are their own; what a previous
        # representative of the same HCPs sent is counted apart and labelled on each item.
        done = (NbaStatus.SENT, NbaStatus.RESPONDED)
        own = dict(
            db.execute(
                select(Nba.status, func.count())
                .where(
                    rbac.nba_filter(user),
                    Nba.status.in_(done),
                    Nba.reviewed_by_user_id == user.id,
                )
                .group_by(Nba.status)
            ).all()
        )
        inherited = sum(by_status.get(st, 0) for st in done) - sum(own.values())
        by_status = {**by_status, **{st: own.get(st, 0) for st in done}, "inherited": inherited}
    items = []
    for n in rows:
        item = out.nba_summary(db, n, with_identity=identity)
        hcp_sent = n.target_type == TargetType.HCP and n.status in (
            NbaStatus.SENT,
            NbaStatus.RESPONDED,
        )
        if hcp_sent and n.reviewed_by_user_id:
            reviewer = db.get(User, n.reviewed_by_user_id)
            item["sent_by"] = (
                "You" if n.reviewed_by_user_id == user.id
                else (reviewer.display_name if reviewer else "A previous representative")
            )  # fmt: skip
        elif hcp_sent:
            item["sent_by"] = "A previous representative"
        item["origin"] = n.origin
        items.append(item)
    return {
        "total": total,
        "counts": by_status,
        "real_waiting": real_waiting if rbac.can_see_target_profile(user) else None,
        # What waits for this care manager (the same figures as the menu count).
        "work": workload.outreach(db, user)
        if rbac.can_review(user, Nba(target_type=TargetType.PATIENT))
        and rbac.can_see_target_profile(user)
        else None,
        "items": items,
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
        raise _conflict("blocked", f"No longer eligible: {nba.block_reason}")

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
    """Wording is the MLR-approved content inside a fixed template frame, so nobody edits it
    here: a change to what the recipient reads is a change to governed content, which goes
    through MLR as a new content version (M-01). The reviewer chooses a variant instead."""
    nba = _reviewable(db, user, nba_id)
    content = db.get(Content, nba.content_id)
    ref = f"{content.content_id} v{content.version}" if content else "the approved content"
    how = (
        "To change it, propose a new version of the content for MLR review."
        if can(user, Permission.CONTENT_PROPOSE)
        else "To change it, ask Compliance to revise the content."
    )
    raise HTTPException(
        status.HTTP_409_CONFLICT,
        {
            "code": "governed_wording",
            "message": f"This wording is MLR-approved content {ref} and cannot be edited. {how}",
            "content_id": nba.content_id,
        },
    )


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
        raise _conflict("blocked", f"Not sent: {exc.reason}") from exc
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
        raise _conflict("not_a_call", "Outcomes are logged by staff only for calls and visits.")
    interaction = db.scalar(
        select(Interaction).where(
            Interaction.nba_id == nba.id, Interaction.outcome == Outcome.PENDING
        )
    )
    if interaction is None:
        raise _conflict("outcome_recorded", "The outcome is already recorded.")
    interaction.outcome_reason, interaction.note = body.reason, body.note
    delivery.capture_response(
        db,
        interaction,
        body.outcome,
        delivery.now_on(db),
        actor=user.username,
        actor_role=user.role,
        note=body.note,
    )
    # The care team recorded this outcome themselves: nothing left for them to look at.
    nba.response_reviewed_ts = utcnow()
    db.commit()
    return out.nba_detail(db, nba, clock.get_today(db))


@router.post("/{nba_id}/response-reviewed")
def response_reviewed(
    nba_id: int, user: User = Depends(readers), db: Session = Depends(get_db)
) -> dict:
    """The care team has looked at a real patient's response; it leaves their counts."""
    nba = _reviewable(db, user, nba_id)
    _require_status(nba, NbaStatus.RESPONDED)
    if nba.response_reviewed_ts is None:
        nba.response_reviewed_ts = utcnow()
        audit.record(
            db, "nba_response_reviewed", "nba", nba.id, nba_id=nba.id, actor=user.username,
            actor_role=user.role,
        )  # fmt: skip
        db.commit()
    return out.nba_detail(db, nba, clock.get_today(db))
