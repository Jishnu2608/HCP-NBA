"""Shapes rows into API responses. One place, so each role sees a consistent set of fields."""

import re
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import pipeline
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
from app.models.enums import NbaStatus, TargetType
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


def hcp_location_label(h: Hcp) -> str | None:
    """Where the HCP practises: a synthetic practice's city and state, or the country (and
    US state) an invited HCP gave when accepting their invitation."""
    country = h.country or ("US" if h.origin == "synthetic" and h.state else None)
    if h.city and h.state:
        return f"{h.city}, {h.state}, {jurisdiction.label(country, None)}"
    return jurisdiction.label(country, h.state if country == "US" else None) if country else None


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


def target_origin(db: Session, nba: Nba) -> str | None:
    if nba.target_type == "PATIENT":
        p = db.get(Patient, nba.target_id)
        return p.origin if p else None
    h = db.get(Hcp, nba.target_id)
    return h.origin if h else None


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
        # A real person (self-registered or clinic patient) rather than a demo record.
        "target_origin": target_origin(db, nba) if with_identity else None,
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
        # The safeguards as they stand now, not as they stood when it was generated: a
        # consent withdrawn or an approval lapsed since then shows before anyone acts.
        "gate_now": gate_now(db, nba),
        "response_reviewed_ts": nba.response_reviewed_ts,
    }


def gate_now(db: Session, nba: Nba) -> dict | None:
    if nba.status not in (NbaStatus.READY_FOR_REVIEW, NbaStatus.APPROVED):
        return None
    from app.nba.revalidate import current_failures

    failures = current_failures(db, nba, include_frequency=False)
    return {"ok": not failures, "codes": failures, "reason": gates.describe(failures) or None}


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
        # The durable identity of the account that acted (an email can be reused later).
        "actor_user_id": a.actor_user_id if identified else None,
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
        response=response_out(db, nba),
    )
    return out


def response_out(db: Session, nba: Nba) -> dict | None:
    """How the person answered what was sent, if they did."""
    i = db.scalar(
        select(Interaction).where(Interaction.nba_id == nba.id).order_by(Interaction.id.desc())
    )
    if i is None:
        return None
    return {"outcome": i.outcome, "at": i.outcome_ts, "sent_at": i.int_ts, "channel": i.channel}


def _latest_snapshot(db: Session, therapy_id: int, today: date) -> AdherenceSnapshot | None:
    return db.scalar(
        select(AdherenceSnapshot)
        .where(AdherenceSnapshot.therapy_id == therapy_id, AdherenceSnapshot.as_of_date <= today)
        .order_by(AdherenceSnapshot.as_of_date.desc(), AdherenceSnapshot.id.desc())
    )


def therapy_out(db: Session, t: PatientTherapy, today: date, *, with_risk: bool) -> dict:
    snap = _latest_snapshot(db, t.id, today)
    if pipeline.needs_refresh(db, t, snap, today):
        # A real patient's figures are always as of today: days pass without any event, and
        # the patient, their care manager and their HCP must all read the same current state.
        pipeline.refresh_patient(db, t.patient_id)
        snap = _latest_snapshot(db, t.id, today)
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
