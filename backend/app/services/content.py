"""Content context: which content versions a reader may see, and approved-content metadata."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock, rbac
from app.core.permissions import Permission, can, can_any
from app.models import Content, Interaction, User
from app.models.enums import MlrStatus, TargetType
from app.services import serializers as out
from app.services.errors import Forbidden, NotFound
from app.services.schemas import ContentOut

# Approved material of one audience: who may read it.
APPROVED_AUDIENCE = {
    Permission.CONTENT_READ_APPROVED_HCP: TargetType.HCP,
    Permission.CONTENT_READ_APPROVED_PATIENT: TargetType.PATIENT,
}
READ = (Permission.CONTENT_READ_ALL, Permission.CONTENT_PROPOSE, *APPROVED_AUDIENCE)


def audiences(user: User) -> set[str]:
    return {a for p, a in APPROVED_AUDIENCE.items() if can(user, p)}


def delivered_by(db: Session, user: User, c: Content) -> bool:
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


def readable(db: Session, user: User, c: Content, today: date) -> bool:
    if c.origin == "submitted" and c.author_user_id == user.id:
        return True
    if can(user, Permission.CONTENT_READ_ALL):
        # A draft is private to its author until it is submitted.
        return c.mlr_status != MlrStatus.DRAFT or c.origin == "library"
    if out.content_out(c, today)["usable"] and c.audience in audiences(user):
        return True
    # A representative keeps access to material they delivered, even once it is withdrawn or
    # superseded: HCP questions about it reach them.
    return can(user, Permission.CONTENT_PROPOSE) and delivered_by(db, user, c)


def scoped_content(db: Session, user: User, content_id: str) -> Content:
    if not can_any(user, *READ):
        raise Forbidden("Your account cannot read content.")
    c = db.get(Content, content_id)
    if c is None or not readable(db, user, c, clock.get_today(db)):
        raise NotFound("Content not found")
    return c


def content_version(db: Session, user: User, content_id: str) -> ContentOut:
    """One content version as the reader may see it: wording, audience, channels, MLR status
    and validity. Usable only when approved, effective and not expired."""
    c = scoped_content(db, user, content_id)
    return ContentOut(**out.content_out(c, clock.get_today(db)))


def approved_content(db: Session, user: User, audience: str | None = None) -> list[ContentOut]:
    """Every content version the reader may use now (approved and in date)."""
    if not can_any(user, *READ):
        raise Forbidden("Your account cannot read content.")
    today = clock.get_today(db)
    query = select(Content).where(Content.mlr_status == MlrStatus.APPROVED)
    if audience:
        query = query.where(Content.audience == audience)
    rows = [out.content_out(c, today) for c in db.scalars(query.order_by(Content.content_id))]
    allowed = None if can(user, Permission.CONTENT_READ_ALL) else audiences(user)
    return [
        ContentOut(**r)
        for r in rows
        if r["usable"] and (allowed is None or r["audience"] in allowed)
    ]
