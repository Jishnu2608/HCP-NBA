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


def load_population(db: Session) -> Population:
    as_of = clock.get_today(db)
    therapies, fills, interactions, consents = (defaultdict(list) for _ in range(4))
    # Only confirmed medications with a known measure and supply count. A medication a
    # patient reported stays out of every score until their care team confirms it.
    for t in db.scalars(
        select(PatientTherapy)
        .where(
            PatientTherapy.review_status == ReviewStatus.CONFIRMED,
            PatientTherapy.measure.is_not(None),
            PatientTherapy.days_supply.is_not(None),
        )
        .order_by(PatientTherapy.id)
    ):
        therapies[t.patient_id].append(t)
    for f in db.scalars(select(MedicationFill).order_by(MedicationFill.fill_date)):
        fills[f.therapy_id].append((f.fill_date, f.days_supply))
    for i in db.scalars(select(Interaction).order_by(Interaction.int_ts, Interaction.id)):
        interactions[(i.target_type, i.target_id)].append(i)
    for c in db.scalars(select(Consent)):
        consents[c.patient_id].append(c)
    return Population(
        as_of=as_of,
        patients={p.patient_id: p for p in db.scalars(select(Patient))},
        hcps={h.hcp_id: h for h in db.scalars(select(Hcp))},
        contents={c.content_id: c for c in db.scalars(select(Content))},
        therapies=therapies,
        fills=fills,
        interactions=interactions,
        consents=consents,
    )
