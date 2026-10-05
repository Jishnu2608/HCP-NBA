"""Loads the observable data the engine works from, grouped for fast per-target access.

Deliberately excludes sim_latent: features, scores and models may only use what a real
deployment could observe.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
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
        # The engine's HCP population is synthetic: an invited HCP has no prescribing or
        # engagement data, and is reached through care routing and their inbox instead.
        hcps=(
            {}
            if patient_id
            else {h.hcp_id: h for h in db.scalars(select(Hcp).where(Hcp.origin == "synthetic"))}
        ),
        contents={} if patient_id else {c.content_id: c for c in db.scalars(select(Content))},
        therapies=therapies,
        fills=fills,
        interactions=interactions,
        consents=consents,
    )
