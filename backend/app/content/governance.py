"""The MLR lifecycle of content, in one place.

A content row is one version. Versions of the same material share `lineage_id`; each new
version names the one it revises in `previous_id`. A representative proposes HCP material
(draft), submits it, and MLR decides: approve, request changes, reject, or later withdraw or
ask for a revision. A decided version never changes: the author opens a new version instead.
Approving a version supersedes the lineage's earlier approved version, and withdrawing or
superseding expires the open recommendations that would have delivered it.

Every transition is validated here and audited with the version, the status before and after
and the account that acted. The API only maps requests onto these functions.
"""

from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import Session

from app import audit
from app.clinical import vocabulary
from app.core import clock
from app.core.jurisdiction import COUNTRIES
from app.core.permissions import Permission, can
from app.llm.validator import CONTENT_ID, URL
from app.models import (
    Content,
    ContentMessage,
    ContentMessageRead,
    ContentReview,
    Hcp,
    HcpSpecialty,
    Interaction,
    MessageDraft,
    Nba,
    User,
)
from app.models.enums import ActionType, Channel, Measure, MlrStatus, NbaStatus, TargetType
from app.models.tables import utcnow

APPROVAL_VALID_DAYS = 730
# HCP material a representative may propose, with the topic family it belongs to.
HCP_ACTIONS = {
    ActionType.SHARE_STUDY: "outcomes",
    ActionType.HCP_EDUCATION: "adherence",
    ActionType.PROGRAM_INFO: "program",
    ActionType.REP_VISIT: "adherence",
}
HCP_CHANNELS = (Channel.EMAIL, Channel.PORTAL, Channel.REP_VISIT)
PERSPECTIVES = ("medical", "legal", "regulatory")
# A version still being written or waiting for MLR: the lineage holds at most one.
OPEN = (MlrStatus.DRAFT, MlrStatus.PENDING)
REVISABLE = (
    MlrStatus.CHANGES_REQUESTED,
    MlrStatus.APPROVED,
    MlrStatus.REJECTED,
    MlrStatus.WITHDRAWN,
    MlrStatus.SUPERSEDED,
)
OPEN_NBA = (NbaStatus.READY_FOR_REVIEW, NbaStatus.APPROVED)

STATUS_LABEL = {
    MlrStatus.DRAFT: "Draft",
    MlrStatus.PENDING: "Submitted, in MLR review",
    MlrStatus.CHANGES_REQUESTED: "Changes requested",
    MlrStatus.APPROVED: "Approved",
    MlrStatus.REJECTED: "Rejected",
    MlrStatus.WITHDRAWN: "Withdrawn",
    MlrStatus.SUPERSEDED: "Superseded by a newer version",
}
DECISION_STATUS = {
    "approve": MlrStatus.APPROVED,
    "request_changes": MlrStatus.CHANGES_REQUESTED,
    "reject": MlrStatus.REJECTED,
    "withdraw": MlrStatus.WITHDRAWN,
    "request_revision": MlrStatus.APPROVED,  # stays deliverable until a new version is approved
}
DECISION_AUDIT = {
    "approve": "content_approved",
    "request_changes": "content_changes_requested",
    "reject": "content_rejected",
    "withdraw": "content_withdrawn",
    "request_revision": "content_revision_requested",
}


def error(code: str, message: str, http: int = status.HTTP_409_CONFLICT, **extra) -> HTTPException:
    """Errors in the application's shape: a code the page can act on and a plain message."""
    return HTTPException(http, {"code": code, "message": message, **extra})


def label(c: Content) -> str:
    return f"{c.content_id} v{c.version}"


def is_expired(c: Content) -> bool:
    return c.expiry_date is not None and c.expiry_date <= clock.today()


def versions(db: Session, lineage_id: str) -> list[Content]:
    return list(
        db.scalars(
            select(Content).where(Content.lineage_id == lineage_id).order_by(Content.version)
        )
    )


def is_author(user: User, c: Content) -> bool:
    """The representative who proposed it; library material is maintained by MLR."""
    if c.origin == "library":
        return can(user, Permission.CONTENT_APPROVE)
    return c.author_user_id == user.id and can(user, Permission.CONTENT_PROPOSE)


def _audit(db: Session, user: User | None, action: str, c: Content, previous: str | None, **extra):
    reason = extra.pop("reason", None)
    audit.record(
        db,
        action,
        "content",
        c.content_id,
        actor=user.username if user else "system",
        actor_role=user.role if user else "system",
        compliance_ok=c.mlr_status == MlrStatus.APPROVED,
        reason=reason,
        detail={
            "version": c.version,
            "lineage_id": c.lineage_id,
            "previous_status": previous,
            "resulting_status": c.mlr_status,
            **extra,
        },
    )


# --- Proposal fields -----------------------------------------------------------------


def _text(body: dict, key: str, limit: int, *, required: bool = False) -> str | None:
    value = (body.get(key) or "").strip() or None
    if required and not value:
        raise error("invalid_field", f"{key.replace('_', ' ').capitalize()} is required.", 422)
    if value and len(value) > limit:
        raise error("invalid_field", f"{key.replace('_', ' ').capitalize()} is too long.", 422)
    return value


def _governed_text(value: str | None, field: str) -> None:
    """Approved material is delivered as written: no links, no references to other content,
    no placeholders."""
    if not value:
        return
    if URL.search(value):
        raise error("invalid_field", f"{field} cannot contain links.", 422)
    if CONTENT_ID.search(value):
        raise error("invalid_field", f"{field} cannot refer to other content ids.", 422)
    if "{{" in value or "}}" in value:
        raise error("invalid_field", f"{field} cannot contain placeholders.", 422)


def clean_fields(body: dict) -> dict:
    """Validated proposal fields. Only HCP material can be proposed."""
    title = _text(body, "title", 200, required=True)
    text = _text(body, "body", 2000, required=True)
    _governed_text(title, "Title")
    _governed_text(text, "The HCP-facing wording")
    action = body.get("action_type")
    if action not in HCP_ACTIONS:
        raise error("invalid_field", "Choose what kind of material this is.", 422)
    channels = list(dict.fromkeys(body.get("channels") or []))
    if not channels or any(ch not in HCP_CHANNELS for ch in channels):
        raise error("invalid_field", "Choose at least one channel: email, portal or visit.", 422)
    measure = body.get("measure") or None
    if measure is not None and measure not in set(Measure):
        raise error("invalid_field", "Unknown therapy area.", 422)
    specialty = body.get("specialty") or None
    if specialty is not None and specialty not in vocabulary.SPECIALTIES:
        raise error("invalid_field", "Unknown specialty.", 422)
    claims = []
    for item in body.get("claims") or []:
        claim = (item.get("text") or "").strip()
        reference = (item.get("reference") or "").strip()
        if not claim and not reference:
            continue
        if not claim or len(claim) > 500 or len(reference) > 500:
            raise error("invalid_field", "Each claim needs its text (500 characters at most).", 422)
        _governed_text(claim, "A claim")
        claims.append({"text": claim, "reference": reference})
    jurisdictions = list(dict.fromkeys(body.get("jurisdictions") or [])) or None
    if jurisdictions and any(j not in COUNTRIES for j in jurisdictions):
        raise error("invalid_field", "Unknown country in where the material may be used.", 422)
    family = HCP_ACTIONS[action]
    topic = (
        f"{measure}_{family}"
        if measure
        else ("general_program" if family == "program" else "general_adherence")
    )
    return {
        "title": title,
        "body": text,
        "audience": TargetType.HCP,
        "action_type": action,
        "topic": topic,
        "measure": measure,
        "specialty": specialty,
        "channels": channels,
        "claims": claims,
        "indication": _text(body, "indication", 2000),
        "safety_info": _text(body, "safety_info", 2000),
        "labelling_note": _text(body, "labelling_note", 2000),
        "jurisdictions": jurisdictions,
    }


def missing_for_review(c: Content) -> list[str]:
    """What a version needs before MLR can review it."""
    missing = []
    claims = c.claims or []
    if not claims:
        missing.append("at least one claim")
    elif any(not (x.get("reference") or "").strip() for x in claims):
        missing.append("a supporting reference for every claim")
    if not c.indication:
        missing.append("the indication")
    if not c.safety_info:
        missing.append("safety and benefit-risk information")
    return missing


def _next_id(db: Session) -> str:
    numbers = [
        int(cid[4:])
        for cid in db.scalars(select(Content.content_id))
        if cid.startswith("CNT_") and cid[4:].isdigit()
    ]
    return f"CNT_{max(numbers, default=0) + 1:03d}"


# --- Author transitions --------------------------------------------------------------


def create_draft(db: Session, user: User, body: dict) -> Content:
    fields = clean_fields(body)
    cid = _next_id(db)
    c = Content(
        content_id=cid,
        version=1,
        lineage_id=cid,
        origin="submitted",
        author_user_id=user.id,
        mlr_status=MlrStatus.DRAFT,
        **fields,
    )
    db.add(c)
    db.flush()
    _audit(db, user, "content_drafted", c, None)
    return c


def update_draft(db: Session, user: User, c: Content, body: dict) -> Content:
    if not is_author(user, c):
        raise error("forbidden", "Only the author can change this proposal.", 403)
    if c.mlr_status != MlrStatus.DRAFT:
        raise error(
            "not_editable",
            f"{label(c)} is {STATUS_LABEL[c.mlr_status].lower()} and no longer changes. "
            "Open a new version to change it.",
        )
    fields = clean_fields(body)
    if c.origin == "library":
        # Library material keeps its audience and routing; MLR edits its wording and facts.
        fields = {
            k: v
            for k, v in fields.items()
            if k not in ("audience", "action_type", "topic", "measure", "specialty", "channels")
        }
    for key, value in fields.items():
        setattr(c, key, value)
    _audit(db, user, "content_draft_updated", c, c.mlr_status)
    return c


def submit(db: Session, user: User, c: Content) -> Content:
    if not is_author(user, c):
        raise error("forbidden", "Only the author can submit this proposal.", 403)
    if c.mlr_status != MlrStatus.DRAFT:
        raise error("not_a_draft", f"{label(c)} was already submitted.")
    missing = missing_for_review(c)
    if missing:
        raise error(
            "incomplete_submission",
            "Before MLR can review it, add " + ", ".join(missing) + ".",
            422,
            missing=missing,
        )
    previous = c.mlr_status
    c.mlr_status, c.submitted_at, c.review_owner_user_id = MlrStatus.PENDING, utcnow(), None
    _audit(db, user, "content_submitted", c, previous)
    return c


def revise(db: Session, user: User, c: Content) -> Content:
    """Opens the next version of a decided version, as a draft copy the author can change."""
    if not is_author(user, c):
        raise error("forbidden", "Only the author can open a new version.", 403)
    if c.mlr_status not in REVISABLE:
        raise error(
            "not_revisable",
            f"{label(c)} is {STATUS_LABEL[c.mlr_status].lower()}: change it directly or wait "
            "for the MLR decision.",
        )
    lineage = versions(db, c.lineage_id)
    open_version = next((v for v in lineage if v.mlr_status in OPEN), None)
    if open_version is not None:
        raise error(
            "revision_open",
            f"Version {open_version.version} ({open_version.content_id}) is already open.",
            content_id=open_version.content_id,
        )
    if c.version != lineage[-1].version:
        raise error("not_latest", f"Open the new version from the latest, v{lineage[-1].version}.")
    new = Content(
        content_id=_next_id(db),
        version=lineage[-1].version + 1,
        lineage_id=c.lineage_id,
        previous_id=c.content_id,
        origin=c.origin,
        author_user_id=c.author_user_id if c.origin == "submitted" else None,
        mlr_status=MlrStatus.DRAFT,
        **{
            k: getattr(c, k)
            for k in (
                "title", "body", "audience", "action_type", "topic", "measure", "specialty",
                "channels", "claims", "indication", "safety_info", "labelling_note",
                "jurisdictions",
            )
        },
    )  # fmt: skip
    db.add(new)
    db.flush()
    _audit(db, user, "content_revision_opened", new, None, revises=c.content_id)
    return new


# --- MLR decisions -------------------------------------------------------------------


def claim_review(db: Session, reviewer: User, c: Content, take_over: bool = False) -> Content:
    """The reviewer working on a submission. Another reviewer must take it over explicitly."""
    if c.mlr_status != MlrStatus.PENDING:
        raise error("not_in_review", f"{label(c)} is not waiting for review.")
    owner = c.review_owner_user_id
    if owner == reviewer.id:
        return c
    if owner is not None and not take_over:
        raise error(
            "review_owned",
            f"Reviewer #{owner} is reviewing {label(c)}. Take it over to continue.",
            owner_user_id=owner,
        )
    c.review_owner_user_id = reviewer.id
    _audit(
        db,
        reviewer,
        "content_review_taken_over" if owner else "content_review_started",
        c,
        c.mlr_status,
        previous_owner_user_id=owner,
    )
    return c


def _perspective(raw: Any, name: str) -> dict | None:
    if raw is None:
        return None
    verdict = raw.get("verdict")
    note = (raw.get("note") or "").strip()
    if verdict not in ("ok", "concern"):
        raise error("invalid_field", f"Choose OK or Concern for the {name} review.", 422)
    if len(note) > 1000:
        raise error("invalid_field", f"The {name} note is too long.", 422)
    if verdict == "concern" and not note:
        raise error("invalid_field", f"Say what the {name} concern is.", 422)
    return {"verdict": verdict, "note": note}


def expire_open_recommendations(
    db: Session, c: Content, reason: str, user: User | None = None
) -> list[int]:
    """Recommendations waiting to deliver this version can no longer be sent."""
    rows = list(
        db.scalars(select(Nba).where(Nba.content_id == c.content_id, Nba.status.in_(OPEN_NBA)))
    )
    for nba in rows:
        nba.status, nba.block_reason = NbaStatus.EXPIRED, reason
    return [n.id for n in rows]


def _refusal(c: Content, decision: str) -> HTTPException:
    s = c.mlr_status
    if s == MlrStatus.APPROVED and decision == "approve":
        if is_expired(c):
            return error(
                "expired_needs_revision",
                f"The approval of {label(c)} expired. Material returns to use only as a new "
                "version reviewed again.",
            )
        return error(
            "already_approved",
            f"{label(c)} is already approved until {c.expiry_date}. An approval covers this "
            "version only; changes need a new version.",
        )
    if s in (MlrStatus.REJECTED, MlrStatus.WITHDRAWN, MlrStatus.SUPERSEDED):
        return error(
            f"{s}_final",
            f"{label(c)} is {STATUS_LABEL[s].lower()}. The author can open a new version from "
            "it, which comes back to MLR.",
        )
    if s in (MlrStatus.DRAFT, MlrStatus.CHANGES_REQUESTED):
        return error(
            "not_submitted",
            f"{label(c)} is {STATUS_LABEL[s].lower()}: it reaches MLR when the author submits.",
        )
    return error("invalid_decision", f"This decision does not apply to {label(c)} now.")


ALLOWED = {
    MlrStatus.PENDING: ("approve", "request_changes", "reject"),
    MlrStatus.APPROVED: ("withdraw", "request_revision"),
}


def decide(
    db: Session,
    reviewer: User,
    c: Content,
    *,
    decision: str,
    expected_status: str | None = None,
    expected_version: int | None = None,
    medical: Any = None,
    legal: Any = None,
    regulatory: Any = None,
    feedback: str | None = None,
    comment: str | None = None,
    take_over: bool = False,
) -> ContentReview:
    if decision not in DECISION_STATUS:
        raise error("invalid_field", "Unknown decision.", 422)
    if (expected_status is not None and c.mlr_status != expected_status) or (
        expected_version is not None and c.version != expected_version
    ):
        raise error(
            "stale_decision",
            f"{label(c)} changed since you opened it: it is now "
            f"{STATUS_LABEL[c.mlr_status].lower()}. Reload to see the current state.",
            status=c.mlr_status,
        )
    if decision not in ALLOWED.get(c.mlr_status, ()):
        raise _refusal(c, decision)
    owner = c.review_owner_user_id
    if c.mlr_status == MlrStatus.PENDING and owner not in (None, reviewer.id):
        if not take_over:
            raise error(
                "review_owned",
                f"Reviewer #{owner} is reviewing {label(c)}. Take it over to decide.",
                owner_user_id=owner,
            )
        claim_review(db, reviewer, c, take_over=True)
    views = {
        name: _perspective(raw, name)
        for name, raw in zip(PERSPECTIVES, (medical, legal, regulatory), strict=True)
    }
    feedback = (feedback or "").strip() or None
    comment = (comment or "").strip() or None
    if feedback and len(feedback) > 2000 or comment and len(comment) > 2000:
        raise error("invalid_field", "Feedback and notes are limited to 2,000 characters.", 422)
    if decision == "approve":
        missing = missing_for_review(c)
        if missing:
            raise error(
                "incomplete_submission",
                "This version cannot be approved without " + ", ".join(missing) + ".",
                422,
            )
        if any(v is None for v in views.values()):
            raise error(
                "perspectives_incomplete",
                "Record the medical, legal and regulatory review before approving.",
                422,
            )
        concerns = [n for n, v in views.items() if v["verdict"] == "concern"]
        if concerns:
            raise error(
                "concern_open",
                "A " + " and ".join(concerns) + " concern is recorded: request changes or "
                "reject instead.",
                422,
            )
    elif not feedback:
        raise error(
            "feedback_required",
            "Write what the author needs to know: the reason and what to change.",
            422,
        )
    if decision == "request_revision" and c.origin == "library":
        raise error(
            "no_author",
            "Library material has no author to ask: open a new version yourself.",
        )

    previous = c.mlr_status
    today = clock.get_today(db)
    extra: dict[str, Any] = {}
    if decision == "approve":
        c.mlr_status = MlrStatus.APPROVED
        c.effective_date = today
        c.expiry_date = today + timedelta(days=APPROVAL_VALID_DAYS)
        c.approved_by_user_id = reviewer.id
        superseded = [
            v
            for v in versions(db, c.lineage_id)
            if v.content_id != c.content_id and v.mlr_status == MlrStatus.APPROVED
        ]
        for old in superseded:
            old_previous = old.mlr_status
            old.mlr_status = MlrStatus.SUPERSEDED
            expired = expire_open_recommendations(
                db, old, f"Content {label(old)} was superseded by {label(c)}"
            )
            _audit(
                db,
                reviewer,
                "content_superseded",
                old,
                old_previous,
                superseded_by=c.content_id,
                recommendations_expired=expired,
            )
        extra["supersedes"] = [o.content_id for o in superseded]
    elif decision == "withdraw":
        c.mlr_status = MlrStatus.WITHDRAWN
        c.withdrawn_reason = feedback
        extra["recommendations_expired"] = expire_open_recommendations(
            db, c, f"Content {label(c)} was withdrawn by MLR: {feedback}"
        )
    else:
        c.mlr_status = DECISION_STATUS[decision]
    c.decided_at = utcnow()
    if not c.review_owner_user_id:
        c.review_owner_user_id = reviewer.id
    review = ContentReview(
        content_id=c.content_id,
        content_version=c.version,
        reviewer_user_id=reviewer.id,
        decision=decision,
        previous_status=previous,
        resulting_status=c.mlr_status,
        feedback=feedback,
        comment=comment,
        **views,
    )
    db.add(review)
    db.flush()
    _audit(
        db,
        reviewer,
        DECISION_AUDIT[decision],
        c,
        previous,
        reason=feedback,
        review_id=review.id,
        perspectives={n: (v or {}).get("verdict") for n, v in views.items()},
        **extra,
    )
    return review


# --- Conversation --------------------------------------------------------------------


def _sent_by(db: Session, user: User) -> Select:
    """Interactions this account delivered (their HCP threads)."""
    return select(Interaction.id).where(Interaction.actor_user_id == user.id)


def visible_messages(db: Session, user: User) -> Select:
    """Messages this account may read: reviewers all; an author the conversation on their
    material; a representative the HCP threads about material they delivered; an HCP their
    own threads."""
    query = select(ContentMessage)
    if can(user, Permission.CONTENT_APPROVE):
        return query
    rules = []
    if can(user, Permission.CONTENT_PROPOSE):
        authored = select(Content.lineage_id).where(Content.author_user_id == user.id)
        rules.append(and_(ContentMessage.hcp_id.is_(None), ContentMessage.lineage_id.in_(authored)))
        rules.append(ContentMessage.interaction_id.in_(_sent_by(db, user)))
    if user.hcp_id:
        rules.append(ContentMessage.hcp_id == user.hcp_id)
    return query.where(or_(*rules)) if rules else query.where(False)


def unread(db: Session, user: User) -> Select:
    read = select(ContentMessageRead.message_id).where(ContentMessageRead.user_id == user.id)
    return (
        visible_messages(db, user)
        .where(ContentMessage.author_user_id != user.id)
        .where(ContentMessage.id.not_in(read))
    )


def mark_read(db: Session, user: User, messages: list[ContentMessage]) -> None:
    read = set(
        db.scalars(
            select(ContentMessageRead.message_id).where(
                ContentMessageRead.user_id == user.id,
                ContentMessageRead.message_id.in_([m.id for m in messages]),
            )
        )
    )
    for m in messages:
        if m.id not in read and m.author_user_id != user.id:
            db.add(ContentMessageRead(message_id=m.id, user_id=user.id))


def post_message(
    db: Session,
    user: User,
    c: Content,
    body: str,
    *,
    hcp_id: str | None = None,
    interaction_id: int | None = None,
) -> ContentMessage:
    text = (body or "").strip()
    if not text or len(text) > 2000:
        raise error("invalid_field", "Write a message of up to 2,000 characters.", 422)
    m = ContentMessage(
        lineage_id=c.lineage_id,
        content_id=c.content_id,
        author_user_id=user.id,
        author_role=user.role,
        body=text,
        hcp_id=hcp_id,
        interaction_id=interaction_id,
    )
    db.add(m)
    db.flush()
    audit.record(
        db,
        "content_message_posted",
        "content",
        c.content_id,
        actor=user.username,
        actor_role=user.role,
        detail={
            "version": c.version,
            "message_id": m.id,
            "hcp_thread": hcp_id is not None,
            "interaction_id": interaction_id,
        },
    )
    return m


# --- Delivery oversight --------------------------------------------------------------


def hcp_labels(db: Session, interactions: list[Interaction]) -> dict[str, dict]:
    """HCPs who received material, without identity: "HCP #n", specialties, country."""
    labels: dict[str, dict] = {}
    ids = []
    for i in sorted(interactions, key=lambda i: i.id):
        if i.target_type == TargetType.HCP and i.target_id not in ids:
            ids.append(i.target_id)
    specialties: dict[str, list[str]] = {}
    for s in db.scalars(select(HcpSpecialty).where(HcpSpecialty.hcp_id.in_(ids))):
        specialties.setdefault(s.hcp_id, []).append(vocabulary.specialty_label(s.specialty))
    for n, hid in enumerate(ids, 1):
        h = db.get(Hcp, hid)
        labels[hid] = {
            "label": f"HCP #{n}",
            "specialties": sorted(specialties.get(hid, [])),
            "country": COUNTRIES.get(h.country, h.country) if h and h.country else None,
        }
    return labels


def deliveries(db: Session, lineage_id: str) -> dict:
    """What happened to every version of this material once approved."""
    rows = versions(db, lineage_id)
    ids = [v.content_id for v in rows]
    by_version = {v.content_id: v.version for v in rows}
    nba_counts = dict(
        db.execute(
            select(Nba.status, func.count()).where(Nba.content_id.in_(ids)).group_by(Nba.status)
        ).all()
    )
    sent = list(
        db.scalars(
            select(Interaction)
            .where(Interaction.content_id.in_(ids), Interaction.source == "nba")
            .order_by(Interaction.int_ts.desc())
        )
    )
    labels = hcp_labels(db, sent)
    outcomes: dict[str, int] = {}
    channels: dict[str, int] = {}
    wording: dict[int, dict] = {}
    items = []
    for i in sent:
        outcomes[i.outcome] = outcomes.get(i.outcome, 0) + 1
        channels[i.channel] = channels.get(i.channel, 0) + 1
        draft = db.get(MessageDraft, i.draft_id) if i.draft_id else None
        if draft is not None:
            key = hash((draft.subject, draft.body))
            wording.setdefault(
                key,
                {
                    "version": by_version.get(i.content_id),
                    "content_id": i.content_id,
                    "channel": i.channel,
                    "subject": draft.subject,
                    "body": draft.body,
                    "times": 0,
                },
            )["times"] += 1
        content = db.get(Content, i.content_id)
        items.append(
            {
                "interaction_id": i.id,
                "content_id": i.content_id,
                "version": by_version.get(i.content_id),
                "recipient": labels.get(i.target_id, {"label": "Patient (identity hidden)"})
                if i.target_type == TargetType.HCP
                else {"label": "Patient (identity hidden)"},
                "channel": i.channel,
                "sent_at": i.int_ts,
                "outcome": i.outcome,
                "outcome_at": i.outcome_ts,
                "wording_recorded": draft is not None,
                "content_status_now": content.mlr_status if content else None,
            }
        )
    return {
        "recommendations": nba_counts,
        "sent": len(sent),
        "outcomes": outcomes,
        "channels": channels,
        "items": items[:100],
        "wording": list(wording.values()),
        "still_in_inboxes_after_withdrawal": sum(
            1
            for it in items
            if it["content_status_now"] in (MlrStatus.WITHDRAWN, MlrStatus.SUPERSEDED)
            and it["channel"] in (Channel.PORTAL, Channel.EMAIL, Channel.SMS)
        ),
    }


# --- Demo reset ----------------------------------------------------------------------

_USER_COLUMNS = {
    Content: ("author_user_id", "review_owner_user_id", "approved_by_user_id"),
    ContentReview: ("reviewer_user_id",),
    ContentMessage: ("author_user_id",),
    ContentMessageRead: ("user_id",),
}


def snapshot(db: Session) -> dict:
    """Content, its versions, MLR decisions and conversations are governance records, not
    demo data: a demo reset keeps them. User columns are kept as emails because account ids
    change in a rebuild."""
    emails = dict(db.execute(select(User.id, User.email)).all())
    saved: dict[str, list] = {}
    for model, columns in _USER_COLUMNS.items():
        rows = []
        for obj in db.scalars(select(model)):
            row = {c.name: getattr(obj, c.name) for c in model.__table__.columns}
            rows.append({"row": row, "emails": {c: emails.get(row[c]) for c in columns}})
        saved[model.__tablename__] = rows
    return saved


def restore_content(saved: dict) -> list[Content]:
    """The content rows, before accounts exist again (user columns are linked later)."""
    rows = []
    for item in saved.get("content", []):
        row = dict(item["row"])
        for column in _USER_COLUMNS[Content]:
            row[column] = None
        rows.append(Content(**row))
    return rows


def restore_records(db: Session, saved: dict) -> None:
    """After accounts are re-created: user links on content, then reviews and conversations."""
    ids = dict(db.execute(select(User.email, User.id)).all())
    interactions = set(db.scalars(select(Interaction.id)))
    for item in saved.get("content", []):
        c = db.get(Content, item["row"]["content_id"])
        for column, email in item["emails"].items():
            setattr(c, column, ids.get(email))
    message_ids: dict[int, int] = {}
    for model in (ContentReview, ContentMessage, ContentMessageRead):
        for item in saved.get(model.__tablename__, []):
            row = dict(item["row"])
            for column, email in item["emails"].items():
                row[column] = ids.get(email)
            if model is ContentMessageRead:
                if row["user_id"] is None or row["message_id"] not in message_ids:
                    continue
                row["message_id"] = message_ids[row["message_id"]]
                db.add(model(**row))
                continue
            old_id = row.pop("id")
            if model is ContentMessage and row.get("interaction_id") not in interactions:
                row["interaction_id"] = None  # the delivery was synthetic demo data
            obj = model(**row)
            db.add(obj)
            if model is ContentMessage:
                db.flush()
                message_ids[old_id] = obj.id
    db.flush()
