"""Content and its MLR lifecycle.

Readers see what their permissions allow: Compliance every submitted version; a representative
or care manager approved, in-date material for their audience, and a representative also
their own proposals in any state. The lifecycle rules live in `app.content.governance`.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import serializers as out
from app.api.deps import not_found, require_permission
from app.api.schemas import StrictBody
from app.clinical import vocabulary
from app.content import governance as gov
from app.core import clock, rbac
from app.core.db import get_db
from app.core.jurisdiction import COUNTRIES
from app.core.permissions import Permission, can
from app.models import (
    Content,
    ContentMessage,
    ContentReview,
    Interaction,
    Nba,
    User,
)
from app.models.enums import MlrStatus, NbaStatus, TargetType
from app.models.tables import utcnow

router = APIRouter(prefix="/api/content", tags=["content"])

APPROVAL_VALID_DAYS = gov.APPROVAL_VALID_DAYS
# Without the read-all permission, an account only sees material cleared for use,
# for the audience its permission names.
APPROVED_AUDIENCE = {
    Permission.CONTENT_READ_APPROVED_HCP: TargetType.HCP,
    Permission.CONTENT_READ_APPROVED_PATIENT: TargetType.PATIENT,
}
readers = require_permission(
    Permission.CONTENT_READ_ALL, Permission.CONTENT_PROPOSE, *APPROVED_AUDIENCE
)
reviewers = require_permission(Permission.CONTENT_APPROVE)
proposers = require_permission(Permission.CONTENT_PROPOSE, Permission.CONTENT_APPROVE)


class Claim(StrictBody):
    text: str = Field(default="", max_length=500)
    reference: str = Field(default="", max_length=500)


class ProposalBody(StrictBody):
    title: str = Field(max_length=200)
    body: str = Field(max_length=2000)
    action_type: str
    channels: list[str] = Field(max_length=3)
    measure: str | None = None
    specialty: str | None = None
    claims: list[Claim] = Field(default_factory=list, max_length=10)
    indication: str | None = Field(default=None, max_length=2000)
    safety_info: str | None = Field(default=None, max_length=2000)
    labelling_note: str | None = Field(default=None, max_length=2000)
    product: str | None = Field(default=None, max_length=120)
    jurisdictions: list[str] | None = Field(default=None, max_length=40)


class Perspective(StrictBody):
    verdict: Literal["ok", "concern"]
    note: str = Field(default="", max_length=1000)


class ReviewBody(StrictBody):
    decision: Literal["approve", "request_changes", "reject", "withdraw", "request_revision"]
    expected_status: str | None = None
    expected_version: int | None = None
    medical: Perspective | None = None
    legal: Perspective | None = None
    regulatory: Perspective | None = None
    # What the author reads; the comment stays with Compliance.
    feedback: str | None = Field(default=None, max_length=2000)
    comment: str | None = Field(default=None, max_length=2000)
    take_over: bool = False


class ClaimBody(StrictBody):
    take_over: bool = False


class MessageBody(StrictBody):
    body: str = Field(min_length=1, max_length=2000)
    # Reply in the thread an HCP started about a delivery (the delivery's id).
    interaction_id: int | None = None


# --- Reading ------------------------------------------------------------------------------


def _waiting_on(db: Session) -> dict[str, int]:
    """content_id -> number of open recommendations held back by that content."""
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
    today = clock.get_today(db)
    for cid in db.scalars(select(Nba.content_id).where(Nba.status == NbaStatus.BLOCKED)):
        if not out.content_out(db.get(Content, cid), today)["usable"]:
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def _names(db: Session, ids: set[int | None]) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: u.display_name for u in db.scalars(select(User).where(User.id.in_(ids)))}


def _audiences(user: User) -> set[str]:
    return {a for p, a in APPROVED_AUDIENCE.items() if can(user, p)}


def _readable(db: Session, user: User, c: Content, today) -> bool:
    if c.origin == "submitted" and c.author_user_id == user.id:
        return True
    if can(user, Permission.CONTENT_READ_ALL):
        # A draft is private to its author until it is submitted.
        return c.mlr_status != MlrStatus.DRAFT or c.origin == "library"
    if out.content_out(c, today)["usable"] and c.audience in _audiences(user):
        return True
    # A representative keeps access to material they delivered, even once it is withdrawn or
    # superseded: HCP questions about it reach them.
    return can(user, Permission.CONTENT_PROPOSE) and _delivered_by(db, user, c)


def _delivered_by(db: Session, user: User, c: Content) -> bool:
    """Delivered to an HCP currently assigned to this representative (by them or whoever had
    the HCP before): the conversation and context come with the HCP."""
    return (
        db.scalar(
            select(Interaction.id).where(
                Interaction.content_id == c.content_id,
                Interaction.target_type == TargetType.HCP,
                Interaction.target_id.in_(rbac.assigned_hcp_ids(user)),
            )
        )
        is not None
    )


def summary(db: Session, user: User, c: Content, today, names: dict[int, str]) -> dict:
    row = out.content_out(c, today)
    latest = db.scalar(
        select(ContentReview)
        .where(ContentReview.content_id == c.content_id)
        .order_by(ContentReview.id.desc())
    )
    mine = c.origin == "submitted" and c.author_user_id == user.id
    row.update(
        {
            "lineage_id": c.lineage_id,
            "previous_id": c.previous_id,
            "origin": c.origin,
            "status_label": gov.STATUS_LABEL.get(c.mlr_status, c.mlr_status),
            "author": names.get(c.author_user_id) if c.author_user_id else None,
            "mine": mine,
            "submitted_at": c.submitted_at,
            "decided_at": c.decided_at,
            "jurisdictions": c.jurisdictions,
            "withdrawn_reason": c.withdrawn_reason,
            "review_owner_user_id": c.review_owner_user_id
            if can(user, Permission.CONTENT_APPROVE)
            else None,
            # A later version exists: earlier ones are history, not open work.
            "is_latest": c.version
            == db.scalar(
                select(func.max(Content.version)).where(Content.lineage_id == c.lineage_id)
            ),
            "delivered_by_you": can(user, Permission.CONTENT_PROPOSE)
            and _delivered_by(db, user, c),
            # New messages (author, MLR, HCP questions and replies) the reader has not opened.
            "unread": db.scalar(
                select(func.count()).select_from(
                    gov.unread(db, user).where(ContentMessage.content_id == c.content_id).subquery()
                )
            ),
            "last_decision": None
            if latest is None
            else {
                "decision": latest.decision,
                "feedback": latest.feedback,
                "ts": latest.ts,
                "new": mine and latest.seen_by_author_at is None,
            },
        }
    )
    return row


@router.get("")
def list_content(user: User = Depends(readers), db: Session = Depends(get_db)) -> list[dict]:
    today = clock.get_today(db)
    rows = [
        c
        for c in db.scalars(select(Content).order_by(Content.content_id))
        if _readable(db, user, c, today)
    ]
    names = _names(db, {c.author_user_id for c in rows})
    result = [summary(db, user, c, today, names) for c in rows]
    if can(user, Permission.CONTENT_READ_ALL):
        waiting = _waiting_on(db)
        for r in result:
            r["recommendations_waiting"] = waiting.get(r["content_id"], 0)
    return result


@router.get("/options")
def options(user: User = Depends(readers)) -> dict:
    """Choices for a proposal form."""
    return {
        "action_types": [
            {"code": a, "label": label}
            for a, label in (
                ("share_study", "Study summary"),
                ("hcp_education", "HCP education"),
                ("program_info", "Support-programme information"),
                ("rep_visit", "Visit discussion guide"),
            )
        ],
        "channels": [
            {"code": "email", "label": "Email"},
            {"code": "portal", "label": "Portal message"},
            {"code": "rep_visit", "label": "In-person visit"},
        ],
        "measures": [
            {"code": "diabetes", "label": "Type 2 diabetes"},
            {"code": "hypertension", "label": "High blood pressure"},
            {"code": "cholesterol", "label": "High cholesterol"},
        ],
        "specialties": vocabulary.specialty_options(),
        "countries": [{"code": c, "name": n} for c, n in COUNTRIES.items() if c != "ZZ"],
    }


def _get(db: Session, user: User, content_id: str) -> Content:
    c = db.get(Content, content_id)
    if c is None or not _readable(db, user, c, clock.get_today(db)):
        raise not_found("Content not found")
    return c


FIELDS = (
    "title", "body", "claims", "indication", "safety_info", "labelling_note", "jurisdictions",
    "channels", "specialty", "measure", "action_type", "product",
)  # fmt: skip


def _changes(db: Session, c: Content) -> list[dict]:
    if not c.previous_id:
        return []
    prev = db.get(Content, c.previous_id)
    return [
        {"field": f, "before": getattr(prev, f), "after": getattr(c, f)}
        for f in FIELDS
        if prev is not None and getattr(prev, f) != getattr(c, f)
    ]


def _review_out(r: ContentReview, user: User, versions: dict[str, int]) -> dict:
    reviewer = can(user, Permission.CONTENT_APPROVE)
    return {
        "id": r.id,
        "kind": "review",
        "content_id": r.content_id,
        "version": r.content_version or versions.get(r.content_id),
        "decision": r.decision,
        "previous_status": r.previous_status,
        "resulting_status": r.resulting_status,
        "medical": r.medical,
        "legal": r.legal,
        "regulatory": r.regulatory,
        "feedback": r.feedback,
        # Internal reasoning stays with Compliance.
        "comment": r.comment if reviewer else None,
        "reviewer": (
            ("You" if r.reviewer_user_id == user.id else f"Reviewer #{r.reviewer_user_id}")
            if reviewer
            else "MLR reviewer"
        ),
        "ts": r.ts,
    }


def _thread_label(db: Session, user: User, m: ContentMessage, labels: dict) -> str:
    if m.author_user_id == user.id:
        return "You"
    if m.author_role == "hcp":
        if can(user, Permission.CONTENT_APPROVE):
            return labels.get(m.hcp_id, {}).get("label", "HCP")
        author = db.get(User, m.author_user_id) if m.author_user_id else None
        return author.display_name if author else "HCP"
    if m.author_role == "compliance":
        return "MLR reviewer"
    author = db.get(User, m.author_user_id) if m.author_user_id else None
    return author.display_name if author else m.author_role


def _messages(db: Session, user: User, c: Content) -> dict:
    rows = list(
        db.scalars(
            gov.visible_messages(db, user)
            .where(ContentMessage.lineage_id == c.lineage_id)
            .order_by(ContentMessage.id)
        )
    )
    deliveries = list(
        db.scalars(
            select(Interaction).where(
                Interaction.id.in_({m.interaction_id for m in rows if m.interaction_id})
            )
        )
    )
    labels = gov.hcp_labels(db, deliveries)
    general, threads = [], {}
    for m in rows:
        item = {
            "id": m.id,
            "kind": "message",
            "content_id": m.content_id,
            "author": _thread_label(db, user, m, labels),
            "author_role": m.author_role,
            "body": m.body,
            "ts": m.ts,
        }
        if m.hcp_id is None:
            general.append(item)
            continue
        key = m.interaction_id
        if key not in threads:
            who = labels.get(m.hcp_id, {})
            if not can(user, Permission.CONTENT_APPROVE):
                author = db.scalar(select(User).where(User.hcp_id == m.hcp_id))
                who = {**who, "label": author.display_name if author else who.get("label")}
            threads[key] = {"interaction_id": key, "hcp": who, "messages": []}
        threads[key]["messages"].append(item)
    gov.mark_read(db, user, rows)
    return {"general": general, "hcp_threads": list(threads.values())}


@router.get("/{content_id}")
def content_detail(
    content_id: str, user: User = Depends(readers), db: Session = Depends(get_db)
) -> dict:
    c = _get(db, user, content_id)
    today = clock.get_today(db)
    lineage = [v for v in gov.versions(db, c.lineage_id) if _readable(db, user, v, today)]
    names = _names(db, {v.author_user_id for v in lineage})
    reviewer = can(user, Permission.CONTENT_APPROVE)
    author = gov.is_author(user, c)
    involved = reviewer or author
    versions = {v.content_id: v.version for v in lineage}
    reviews = (
        list(
            db.scalars(
                select(ContentReview)
                .where(ContentReview.content_id.in_(list(versions)))
                .order_by(ContentReview.id)
            )
        )
        if involved
        else []
    )
    if author and c.origin == "submitted":
        # The author has now seen every decision on this material.
        for r in reviews:
            if r.seen_by_author_at is None:
                r.seen_by_author_at = utcnow()
    allowed = list(gov.ALLOWED.get(c.mlr_status, ())) if reviewer else []
    if "request_revision" in allowed and c.origin == "library":
        allowed.remove("request_revision")
    if c.mlr_status == MlrStatus.APPROVED and gov.is_expired(c) and "approve" in allowed:
        allowed.remove("approve")
    open_version = next((v for v in lineage if v.mlr_status in gov.OPEN), None)
    result = {
        **summary(db, user, c, today, names),
        "claims": c.claims or [],
        "indication": c.indication,
        "safety_info": c.safety_info,
        "labelling_note": c.labelling_note,
        "jurisdiction_names": [COUNTRIES.get(j, j) for j in (c.jurisdictions or [])],
        "missing_for_review": gov.missing_for_review(c) if involved else [],
        "versions": [
            {
                "content_id": v.content_id,
                "version": v.version,
                "status": v.mlr_status,
                "status_label": gov.STATUS_LABEL.get(v.mlr_status, v.mlr_status),
                "submitted_at": v.submitted_at,
                "decided_at": v.decided_at,
                "effective_date": v.effective_date,
                "expiry_date": v.expiry_date,
            }
            for v in lineage
        ],
        "changes": _changes(db, c) if involved else [],
        "reviews": [_review_out(r, user, versions) for r in reviews],
        "conversation": _messages(db, user, c),
        "actions": {
            "edit": author and c.mlr_status == MlrStatus.DRAFT,
            "submit": author and c.mlr_status == MlrStatus.DRAFT,
            "revise": author
            and c.mlr_status in gov.REVISABLE
            and open_version is None
            and c.version == max(versions.values()),
            "open_version": open_version.content_id if open_version else None,
            "decide": allowed,
            "claim": reviewer and c.mlr_status == MlrStatus.PENDING,
            "message": involved,
        },
        "deliveries": gov.deliveries(db, c.lineage_id) if reviewer else None,
    }
    if reviewer:
        result["recommendations_waiting"] = _waiting_on(db).get(c.content_id, 0)
    db.commit()
    return result


# --- Author actions -----------------------------------------------------------------------


def _authored(db: Session, user: User, content_id: str) -> Content:
    c = db.get(Content, content_id)
    if c is None or not gov.is_author(user, c):
        raise not_found("Content not found")
    return c


def _done(db: Session, user: User, c: Content) -> dict:
    db.commit()
    return content_detail(c.content_id, user, db)


@router.post("", status_code=201)
def propose(
    body: ProposalBody,
    user: User = Depends(require_permission(Permission.CONTENT_PROPOSE)),
    db: Session = Depends(get_db),
) -> dict:
    return _done(db, user, gov.create_draft(db, user, body.model_dump()))


@router.patch("/{content_id}")
def update(
    content_id: str,
    body: ProposalBody,
    user: User = Depends(proposers),
    db: Session = Depends(get_db),
) -> dict:
    c = _authored(db, user, content_id)
    return _done(db, user, gov.update_draft(db, user, c, body.model_dump()))


@router.post("/{content_id}/submit")
def submit(content_id: str, user: User = Depends(proposers), db: Session = Depends(get_db)) -> dict:
    return _done(db, user, gov.submit(db, user, _authored(db, user, content_id)))


@router.post("/{content_id}/revise", status_code=201)
def revise(content_id: str, user: User = Depends(proposers), db: Session = Depends(get_db)) -> dict:
    return _done(db, user, gov.revise(db, user, _authored(db, user, content_id)))


# --- MLR ----------------------------------------------------------------------------------


def _reviewable(db: Session, content_id: str) -> Content:
    c = db.get(Content, content_id)
    if c is None or (c.mlr_status == MlrStatus.DRAFT and c.origin == "submitted"):
        raise not_found("Content not found")
    return c


@router.post("/{content_id}/claim-review")
def claim_review(
    content_id: str,
    body: ClaimBody,
    user: User = Depends(reviewers),
    db: Session = Depends(get_db),
) -> dict:
    c = _reviewable(db, content_id)
    gov.claim_review(db, user, c, take_over=body.take_over)
    return _done(db, user, c)


@router.post("/{content_id}/review")
def review_content(
    content_id: str,
    body: ReviewBody,
    user: User = Depends(reviewers),
    db: Session = Depends(get_db),
) -> dict:
    """MLR decision. Requires the content-approval permission, which only Compliance holds."""
    c = _reviewable(db, content_id)
    data: dict[str, Any] = body.model_dump()
    gov.decide(db, user, c, **data)
    return _done(db, user, c)


# --- Conversation -------------------------------------------------------------------------


@router.post("/{content_id}/messages", status_code=201)
def post_message(
    content_id: str,
    body: MessageBody,
    user: User = Depends(proposers),
    db: Session = Depends(get_db),
) -> dict:
    c = db.get(Content, content_id)
    if c is None:
        raise not_found("Content not found")
    reviewer = can(user, Permission.CONTENT_APPROVE)
    if body.interaction_id is None:
        if not (reviewer or gov.is_author(user, c)):
            raise not_found("Content not found")
        gov.post_message(db, user, c, body.body)
        return _done(db, user, c)
    # A reply in an HCP's thread about a delivery of this material.
    delivery = db.get(Interaction, body.interaction_id)
    started = db.scalar(
        select(ContentMessage).where(
            ContentMessage.interaction_id == body.interaction_id,
            ContentMessage.lineage_id == c.lineage_id,
        )
    )
    if (
        delivery is None
        or started is None
        or delivery.target_type != TargetType.HCP
        or not (reviewer or delivery.target_id in set(db.scalars(rbac.assigned_hcp_ids(user))))
    ):
        raise not_found("Conversation not found")
    gov.post_message(db, user, c, body.body, hcp_id=delivery.target_id, interaction_id=delivery.id)
    db.commit()
    if not _readable(db, user, c, clock.get_today(db)):
        return {"posted": True}
    return content_detail(c.content_id, user, db)
