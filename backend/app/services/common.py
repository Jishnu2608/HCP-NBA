"""Pieces shared by several read services: consent rows, interaction history, the open
recommendation of a person."""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import Permission, can
from app.models import Consent, Content, Interaction, Nba, User
from app.models.enums import NbaStatus, TargetType
from app.services import serializers as out
from app.services.schemas import ConsentOut, InteractionOut, RecommendationSummary

# Recommendations that are still in play for a person.
LIVE = (NbaStatus.READY_FOR_REVIEW, NbaStatus.APPROVED, NbaStatus.BLOCKED)
HISTORY_LIMIT = 40


def consent_out(c: Consent, today: date) -> dict:
    in_effect = c.effective_from <= today and (c.effective_to is None or today < c.effective_to)
    return {
        "purpose": c.purpose,
        "channel": c.channel,
        "granted": c.granted,
        "effective_from": c.effective_from,
        "effective_to": c.effective_to,
        "in_effect": in_effect,
        "source": c.source,
    }


def consents(db: Session, patient_id: str, today: date) -> list[ConsentOut]:
    rows = db.scalars(
        select(Consent).where(Consent.patient_id == patient_id).order_by(Consent.effective_from)
    )
    return [ConsentOut(**consent_out(c, today)) for c in rows]


def sent_by(db: Session, reader: User, actor_user_id: int | None, source: str) -> str | None:
    """Who carried out an engine or representative touch, as the reader should see it. Work
    done by someone no longer responsible for the HCP is labelled, never shown as the
    reader's own."""
    if source not in ("nba", "rep"):
        return None
    if actor_user_id == reader.id:
        return "You"
    actor = db.get(User, actor_user_id) if actor_user_id else None
    if actor is None or not can(actor, Permission.HCP_READ_ASSIGNED):
        return "A previous representative" if actor is None else actor.display_name
    return actor.display_name


def history(
    db: Session,
    target_type: str,
    target_id: str,
    reader: User | None = None,
    limit: int = HISTORY_LIMIT,
) -> list[InteractionOut]:
    """The person's latest interactions, newest first (bounded)."""
    rows = db.scalars(
        select(Interaction)
        .where(Interaction.target_type == target_type, Interaction.target_id == target_id)
        .order_by(Interaction.int_ts.desc())
        .limit(limit)
    ).all()
    contents = {
        c.content_id: c
        for c in db.scalars(
            select(Content).where(Content.content_id.in_({r.content_id for r in rows}))
        )
    }
    items = []
    for r in rows:
        item = out.interaction_out(r, contents)
        if reader is not None and target_type == TargetType.HCP:
            item["sent_by"] = sent_by(db, reader, r.actor_user_id, r.source)
        items.append(InteractionOut(**item))
    return items


def open_recommendation(
    db: Session, target_type: str, target_id: str
) -> RecommendationSummary | None:
    nba = db.scalar(
        select(Nba)
        .where(Nba.target_type == target_type, Nba.target_id == target_id, Nba.status.in_(LIVE))
        .order_by(Nba.id.desc())
    )
    return RecommendationSummary(**out.nba_summary(db, nba)) if nba else None
