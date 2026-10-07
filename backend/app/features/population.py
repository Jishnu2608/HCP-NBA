"""Loads the observable data the engine works from, grouped for fast per-target access.

Deliberately excludes sim_latent: features, scores and models may only use what a real
deployment could observe.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import clock
from app.models import (
    Consent,
    Content,
    Hcp,
    Interaction,
    MedicationFill,
    Patient,
    PatientTherapy,
)
from app.models.enums import ReviewStatus


@dataclass
class Population:
    as_of: date
    patients: dict[str, Patient]
    hcps: dict[str, Hcp]
    contents: dict[str, Content]
    therapies: dict[str, list[PatientTherapy]] = field(default_factory=dict)
    fills: dict[int, list[tuple[date, int]]] = field(default_factory=dict)
    interactions: dict[tuple[str, str], list[Interaction]] = field(default_factory=dict)
    consents: dict[str, list[Consent]] = field(default_factory=dict)
    # Every specialty each HCP holds (`hcp_specialty`).
    hcp_specialties: dict[str, set[str]] = field(default_factory=dict)
    # Open commercial work per HCP (their request, a follow-up, a meeting): while any is open
    # the representative owes that next step, so the engine proposes no other outreach.
    hcp_open_work: dict[str, list] = field(default_factory=dict)


def _hcp_open_work(db: Session) -> dict[str, list]:
    from app.models import HcpTask

    out: dict[str, list] = defaultdict(list)
    for t in db.scalars(select(HcpTask).where(HcpTask.status.in_(("open", "scheduled")))):
        out[t.hcp_id].append(t)
    return dict(out)


def _engine_hcps(db: Session) -> dict:
    from app.models import User
    from app.models.enums import AccountStatus

    active = select(User.hcp_id).where(
        User.hcp_id.is_not(None), User.status == AccountStatus.ACTIVE
    )
    return {
        h.hcp_id: h
        for h in db.scalars(
            select(Hcp).where(or_(Hcp.origin == "synthetic", Hcp.hcp_id.in_(active)))
        )
    }


def _hcp_specialties(db: Session) -> dict[str, set[str]]:
    from app.models import HcpSpecialty

    out: dict[str, set[str]] = defaultdict(set)
    for hcp_id, specialty in db.execute(select(HcpSpecialty.hcp_id, HcpSpecialty.specialty)):
        out[hcp_id].add(specialty)
    return dict(out)


def load_population(db: Session, *, patient_id: str | None = None) -> Population:
    """Everything the engine may observe. With `patient_id`, only that patient's data (used to
    refresh one patient's adherence at once); HCPs and content are then left empty."""
    as_of = clock.get_today(db)
    therapies, fills, interactions, consents = (defaultdict(list) for _ in range(4))

    def one(query, column):
        return query.where(column == patient_id) if patient_id else query

    # Only confirmed medications with a known measure and supply count. A medication a
    # patient reported stays out of every score until their care team confirms it.
    for t in db.scalars(
        one(
            select(PatientTherapy).where(
                PatientTherapy.review_status == ReviewStatus.CONFIRMED,
                PatientTherapy.measure.is_not(None),
                PatientTherapy.days_supply.is_not(None),
            ),
            PatientTherapy.patient_id,
        ).order_by(PatientTherapy.id)
    ):
        therapies[t.patient_id].append(t)
    for f in db.scalars(
        one(select(MedicationFill), MedicationFill.patient_id).order_by(MedicationFill.fill_date)
    ):
        fills[f.therapy_id].append((f.fill_date, f.days_supply))
    for i in db.scalars(
        one(select(Interaction), Interaction.target_id).order_by(Interaction.int_ts, Interaction.id)
    ):
        interactions[(i.target_type, i.target_id)].append(i)
    for c in db.scalars(one(select(Consent), Consent.patient_id)):
        consents[c.patient_id].append(c)
    return Population(
        as_of=as_of,
        patients={p.patient_id: p for p in db.scalars(one(select(Patient), Patient.patient_id))},
        # The engine's HCP population: synthetic HCPs and invited HCPs whose account is
        # active. An invited HCP has no history; the same gates, models and review apply.
        hcps=({} if patient_id else _engine_hcps(db)),
        hcp_specialties=({} if patient_id else _hcp_specialties(db)),
        hcp_open_work=({} if patient_id else _hcp_open_work(db)),
        contents={} if patient_id else {c.content_id: c for c in db.scalars(select(Content))},
        therapies=therapies,
        fills=fills,
        interactions=interactions,
        consents=consents,
    )
