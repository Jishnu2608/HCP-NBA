"""A patient's or HCP's own inbox: messages delivered through the engine, newest first.

Read only: marking replies as read is the screen's decision (api/me.py)."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import Permission, can
from app.models import (
    Content,
    ContentMessage,
    Interaction,
    MessageDraft,
    Nba,
    PatientTherapy,
    User,
)
from app.models.enums import Channel, MlrStatus, Outcome, ReviewStatus, TargetType
from app.services.errors import Forbidden, NoRecord, NotFound
from app.services.schemas import InboxItem

# Messages a person would actually receive. Calls and visits are not inbox items.
INBOX_CHANNELS = (Channel.PORTAL, Channel.EMAIL, Channel.SMS)
# Delivery is simulated in this deployment: an email or text is shown here, never sent out.
CHANNEL_NOTE = {
    Channel.EMAIL: "Email (copy shown here; not sent outside this demo)",
    Channel.SMS: "Text message (copy shown here; not sent outside this demo)",
    Channel.PORTAL: "Portal message",
}
# The most an inbox returns at once; older items are reached with `before`.
MAX_ITEMS = 500


def owner(user: User) -> tuple[str, str]:
    """The single record this account is linked to. Never taken from the request."""
    if not can(user, Permission.SELF_INBOX):
        raise Forbidden("Your account has no inbox.")
    if user.patient_id is not None:
        return TargetType.PATIENT, user.patient_id
    if user.hcp_id is not None:
        return TargetType.HCP, user.hcp_id
    raise NoRecord("No record is linked to this account yet. Ask an administrator.")


def _therapy_active(db: Session, therapy_id: int) -> bool:
    therapy = db.get(PatientTherapy, therapy_id)
    return therapy is not None and therapy.review_status == ReviewStatus.CONFIRMED


def content_notice(content: Content | None) -> str | None:
    """Material withdrawn or replaced after it was delivered stays readable, marked as such."""
    if content is None:
        return None
    if content.mlr_status == MlrStatus.WITHDRAWN:
        why = f" Reason: {content.withdrawn_reason}" if content.withdrawn_reason else ""
        return f"Withdrawn by the medical, legal and regulatory review: no longer current.{why}"
    if content.mlr_status == MlrStatus.SUPERSEDED:
        return "A newer approved version has replaced this material."
    return None


def _hcp_source(db: Session, i: Interaction, content: Content | None) -> dict:
    """An HCP sees which approved content an item is and who sent it (their representative)."""
    if i.target_type != TargetType.HCP:
        return {}
    nba = db.get(Nba, i.nba_id) if i.nba_id else None
    sender = db.get(User, nba.reviewed_by_user_id) if nba and nba.reviewed_by_user_id else None
    thread = list(
        db.scalars(
            select(ContentMessage)
            .where(ContentMessage.interaction_id == i.id, ContentMessage.hcp_id == i.target_id)
            .order_by(ContentMessage.id)
        )
    )
    return {
        "content_title": content.title if content else None,
        "content_version": content.version if content else None,
        "sent_by": sender.display_name if sender else None,
        "conversation": [
            {
                "id": m.id,
                "author": "You"
                if m.author_role == "hcp"
                else "Medical, legal and regulatory review"
                if m.author_role == "compliance"
                else (sender.display_name if sender else "Your representative"),
                "body": m.body,
                "ts": m.ts,
            }
            for m in thread
        ],
        "can_ask": content is not None,
    }


def item(db: Session, i: Interaction) -> InboxItem:
    # The wording actually delivered (recorded at send); older sends fall back to the selection.
    draft = db.get(MessageDraft, i.draft_id) if i.draft_id else None
    draft = draft or db.scalar(
        select(MessageDraft).where(MessageDraft.nba_id == i.nba_id, MessageDraft.is_selected)
    )
    content = db.get(Content, i.content_id) if i.content_id else None
    return InboxItem(
        **{
            "id": i.id,
            "received": i.int_ts,
            "channel": i.channel,
            # A text message has no subject line; never show the internal content title.
            "subject": draft.subject if draft else (content.title if content else None),
            "body": draft.body if draft else (content.body if content else ""),
            "status": i.outcome,
            "responded": i.outcome_ts,
            "can_refill": i.therapy_id is not None
            and i.outcome != Outcome.FILLED
            and _therapy_active(db, i.therapy_id),
            "channel_note": CHANNEL_NOTE.get(i.channel),
            "content_notice": content_notice(content),
            "product": content.product if content else None,
            # The HCP's own answer to the material (interested, request a meeting, ...).
            "intent": i.intent,
        }
        | _hcp_source(db, i, content)
    )


def own_item(db: Session, user: User, interaction_id: int) -> Interaction:
    """One delivered message of the reader's own inbox (anyone else's looks missing)."""
    target_type, target_id = owner(user)
    i = db.get(Interaction, interaction_id)
    if (
        i is None
        or i.target_type != target_type
        or i.target_id != target_id
        or i.source != "nba"
        or i.channel not in INBOX_CHANNELS
    ):
        raise NotFound("Message not found")
    return i


def inbox(
    db: Session, user: User, *, limit: int = MAX_ITEMS, before: int | None = None
) -> list[InboxItem]:
    """The reader's own delivered messages, newest first, at most `limit` (older ones with
    `before`, the id of the oldest item already shown)."""
    target_type, target_id = owner(user)
    query = select(Interaction).where(
        Interaction.target_type == target_type,
        Interaction.target_id == target_id,
        Interaction.source == "nba",
        Interaction.channel.in_(INBOX_CHANNELS),
    )
    if before is not None:
        anchor = db.get(Interaction, before)
        if anchor is not None:
            query = query.where(
                (Interaction.int_ts < anchor.int_ts)
                | ((Interaction.int_ts == anchor.int_ts) & (Interaction.id < anchor.id))
            )
    rows = db.scalars(
        query.order_by(Interaction.int_ts.desc(), Interaction.id.desc()).limit(
            min(limit, MAX_ITEMS)
        )
    )
    return [item(db, i) for i in rows]
