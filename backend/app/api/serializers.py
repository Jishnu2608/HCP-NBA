"""Shapes rows into API responses. One place, so each role sees a consistent set of fields."""

import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import jurisdiction
from app.models import (
    AdherenceSnapshot,
    AuditLog,
    Content,
    FeatureSnapshot,
    Hcp,
    Interaction,
    MessageDraft,
    Nba,
    NbaCandidate,
    Patient,
    PatientTherapy,
)
from app.models.enums import TargetType
from app.nba import gates
from app.nba.rationale import ACTION_LABEL, CHANNEL_LABEL


def patient_name(p: Patient) -> str:
    return f"{p.first_name} {p.last_name}".strip()


def location_label(p: Patient) -> str | None:
    """Where the patient lives. A synthetic record carries a generated city and state; a
    real person's record only what they (or their care manager) gave: country and US state.
    Nothing is filled in from anywhere else."""
    if p.city and p.state:
        return f"{p.city}, {p.state}"
    return jurisdiction.label(p.country, p.region)


def patient_brief(p: Patient) -> dict:
    """Location fields shared by every patient view."""
    return {
        "origin": p.origin,
        "city": p.city,
        "state": p.state,
        "country": p.country,
        "region": p.region,
        "location": location_label(p),
    }


def hcp_name(h: Hcp) -> str:
    return f"Dr. {h.first_name} {h.last_name}".strip()


def target_label(db: Session, target_type: str, target_id: str) -> tuple[str, str | None]:
    """(display name, segment label) for a recommendation's target."""
    if target_type == TargetType.PATIENT:
        p = db.get(Patient, target_id)
        return patient_name(p), p.risk_segment
    h = db.get(Hcp, target_id)
    return hcp_name(h), h.segment


def content_out(c: Content, today: date) -> dict:
    expired = c.expiry_date is not None and c.expiry_date <= today
    return {
        "content_id": c.content_id,
        "version": c.version,
        "title": c.title,
        "body": c.body,
        "audience": c.audience,
        "action_type": c.action_type,
        "topic": c.topic,
        "measure": c.measure,
        "specialty": c.specialty,
        "channels": c.channels,
        "mlr_status": c.mlr_status,
        "effective_date": c.effective_date,
        "expiry_date": c.expiry_date,
        "is_expired": expired,
        "usable": c.mlr_status == "approved" and not expired and c.effective_date is not None,
    }


def nba_summary(db: Session, nba: Nba, *, with_identity: bool = True) -> dict:
    """`with_identity=False` is the gate-outcome view (Compliance): the recommendation and
    its gate results, without anything that identifies the person concerned."""
    name, segment = target_label(db, nba.target_type, nba.target_id)
    content = db.get(Content, nba.content_id) if nba.content_id else None
    return {
        "id": nba.id,
        "cycle_id": nba.cycle_id,
        "target_type": nba.target_type,
        "target_id": nba.target_id if with_identity else None,
        "target_name": name if with_identity else None,
        "segment": segment if with_identity else None,
        "action": nba.action,
        "action_label": ACTION_LABEL.get(nba.action, nba.action),
        "channel": nba.channel,
        "channel_label": CHANNEL_LABEL.get(nba.channel, nba.channel),
        "status": nba.status,
        "score": nba.score,
        "priority": nba.priority,
        "scheduled_for": nba.scheduled_for,
        "timing_note": nba.timing_note,
        "rationale_summary": nba.rationale_summary,
        "content_id": nba.content_id,
        "content_title": content.title if content else None,
        "has_withheld": nba.withheld is not None,
        "block_reason": nba.block_reason,
        "as_of_date": nba.as_of_date,
    }


def draft_out(d: MessageDraft) -> dict:
    return {
        "id": d.id,
        "variant_no": d.variant_no,
        "subject": d.subject,
        "body": d.body,
        "provider": d.provider,
        "model": d.model,
        "is_selected": d.is_selected,
        "edited": d.edited_by_user_id is not None,
    }


_EMAIL = re.compile(r"[^@\s\"']+@[^@\s\"']+\.[^@\s\"']+")
_RECORD_ID = re.compile(r"\b(PAT_R?\d+|HCP_\d+)\b")


def redact(value: Any) -> Any:
    """Masks email addresses and patient / HCP ids anywhere inside an audit value."""
    if isinstance(value, str):
        return _RECORD_ID.sub("[record]", _EMAIL.sub("[email]", value))
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def audit_out(
    a: AuditLog, *, identified: bool = True, verified_actors: set[str] | None = None
) -> dict:
    """`identified=False` (readers without audit:read:identified) masks account emails and
    patient / HCP ids, keeping what happened, when, and the actor's role."""
    row = {
        "id": a.id,
        "ts": a.ts,
        "nba_id": a.nba_id,
        "entity_type": a.entity_type,
        "entity_id": a.entity_id,
        "action": a.action,
        "actor": a.actor,
        "actor_role": a.actor_role,
        "actor_verified": a.actor in (verified_actors or set()),
        "compliance_ok": a.compliance_ok,
        "consent_ok": a.consent_ok,
        "reason": a.reason,
        "detail": a.detail,
    }
    if not identified:
        for key in ("entity_id", "actor", "reason", "detail"):
            row[key] = redact(row[key])
    return row


def interaction_out(i: Interaction, contents: dict[str, Content] | None = None) -> dict:
    content = (contents or {}).get(i.content_id)
    return {
        "id": i.id,
        "ts": i.int_ts,
        "channel": i.channel,
        "channel_label": CHANNEL_LABEL.get(i.channel, i.channel),
        "type": i.type,
        "type_label": ACTION_LABEL.get(i.type, i.type),
        "outcome": i.outcome,
        "outcome_ts": i.outcome_ts,
        "content_id": i.content_id,
        "content_title": content.title if content else None,
        "source": i.source,
        "nba_id": i.nba_id,
    }


def nba_detail(db: Session, nba: Nba, today: date, *, with_identity: bool = True) -> dict:
    out = nba_summary(db, nba, with_identity=with_identity)
    content = db.get(Content, nba.content_id)
    candidates = db.scalars(
        select(NbaCandidate).where(NbaCandidate.nba_id == nba.id).order_by(NbaCandidate.rank)
    ).all()
    titles = {
        c.content_id: c.title
        for c in db.scalars(
            select(Content).where(Content.content_id.in_({c.content_id for c in candidates}))
        )
    }
    out.update(
        rationale=nba.rationale,
        reason_codes=nba.reason_codes,
        predicted=nba.predicted,
        withheld=(
            {**nba.withheld, "explanation": gates.describe(nba.withheld["failures"])}
            if nba.withheld
            else None
        ),
        content=content_out(content, today),
        candidates=[
            {
                "rank": c.rank,
                "action": c.action,
                "action_label": ACTION_LABEL.get(c.action, c.action),
                "channel": c.channel,
                "channel_label": CHANNEL_LABEL.get(c.channel, c.channel),
                "content_id": c.content_id,
                "content_title": titles.get(c.content_id),
                "score": c.score,
                "eligible": c.eligible,
                "gate_failures": c.gate_failures,
                "gate_text": gates.describe(c.gate_failures) if c.gate_failures else None,
                "chosen": (c.action, c.channel, c.content_id)
                == (nba.action, nba.channel, nba.content_id),
            }
            for c in candidates
        ],
        # Drafts address the person by name, so the gate-outcome view leaves them out.
        drafts=[
            draft_out(d)
            for d in db.scalars(
                select(MessageDraft)
                .where(MessageDraft.nba_id == nba.id)
                .order_by(MessageDraft.variant_no)
            )
        ]
        if with_identity
        else [],
        audit=[
            audit_out(a, identified=with_identity)
            for a in db.scalars(
                select(AuditLog).where(AuditLog.nba_id == nba.id).order_by(AuditLog.id)
            )
        ],
        reviewed_ts=nba.reviewed_ts,
    )
    return out


def therapy_out(db: Session, t: PatientTherapy, today: date, *, with_risk: bool) -> dict:
    snap = db.scalar(
        select(AdherenceSnapshot)
        .where(AdherenceSnapshot.therapy_id == t.id, AdherenceSnapshot.as_of_date <= today)
        .order_by(AdherenceSnapshot.as_of_date.desc())
    )
    out = {
        "therapy_id": t.id,
        "measure": t.measure,
        "drug_name": t.drug_name,
        "start_date": t.start_date,
        "days_supply": t.days_supply,
        "prescriber_hcp_id": t.prescriber_hcp_id,
        "pdc": snap.pdc if snap else None,
        "gap_days": snap.gap_days if snap else None,
        "last_fill_date": snap.last_fill_date if snap else None,
        "adherent": snap.pdc >= 0.8 if snap else None,
    }
    if with_risk:
        out.update(
            copay=t.copay,
            mpr=snap.mpr if snap else None,
            risk_score=snap.risk_score if snap else None,
            risk_segment=snap.risk_segment if snap else None,
        )
    return out


def features_out(db: Session, target_type: str, target_id: str, today: date) -> dict | None:
    snap = db.scalar(
        select(FeatureSnapshot)
        .where(
            FeatureSnapshot.target_type == target_type,
            FeatureSnapshot.target_id == target_id,
            FeatureSnapshot.as_of_date <= today,
        )
        .order_by(FeatureSnapshot.as_of_date.desc())
    )
    return snap.features if snap else None
