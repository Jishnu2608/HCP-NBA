from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core import clock
from app.datagen.generate import GenConfig, generate
from app.datagen.validate import validate
from app.models import (
    Consent,
    Content,
    Interaction,
    MedicationFill,
    PatientTherapy,
    SimLatent,
    User,
)
from app.models.enums import MlrStatus, Role

# Synthetic history ends on the day it is generated.
DEFAULT_AS_OF = clock.today()

SMALL = GenConfig(seed=7, n_patients=250, n_hcps=40, n_reps=6, n_care_managers=3)


@pytest.fixture
def seeded(db):
    generate(db, SMALL)
    return db


def _fingerprint(db):
    fills = db.execute(
        select(MedicationFill.patient_id, MedicationFill.fill_date).order_by(MedicationFill.id)
    ).all()
    touches = db.execute(
        select(Interaction.target_id, Interaction.int_ts, Interaction.outcome).order_by(
            Interaction.id
        )
    ).all()
    return fills, touches


def test_validation_passes(seeded):
    failed = [c for c in validate(seeded) if not c.passed]
    assert not failed, failed


def test_generation_is_deterministic_and_replaces_data(db):
    generate(db, SMALL)
    first = _fingerprint(db)
    generate(db, SMALL)
    assert _fingerprint(db) == first


def test_different_seed_changes_data(db):
    generate(db, SMALL)
    first = _fingerprint(db)
    generate(db, GenConfig(seed=8, n_patients=250, n_hcps=40, n_reps=6, n_care_managers=3))
    assert _fingerprint(db) != first


def test_hero_patient_has_widening_gap(seeded):
    fills = list(
        seeded.scalars(
            select(MedicationFill)
            .where(MedicationFill.patient_id == "PAT_00001")
            .order_by(MedicationFill.fill_date)
        )
    )
    assert len(fills) == 7
    last = fills[-1]
    assert last.fill_date + timedelta(days=last.days_supply + 16) == DEFAULT_AS_OF
    lateness = [
        (b.fill_date - a.fill_date).days - a.days_supply
        for a, b in zip(fills, fills[1:], strict=False)
    ]
    assert lateness == [0, 2, 3, 5, 8, 12]


def test_hero_consent_and_primary_non_adherence(seeded):
    sms = seeded.scalar(
        select(Consent).where(Consent.patient_id == "PAT_00002", Consent.channel == "sms")
    )
    assert sms.granted is False
    opted_out = seeded.scalars(
        select(Consent.granted).where(
            Consent.patient_id == "PAT_00004", Consent.channel.is_not(None)
        )
    ).all()
    assert opted_out and not any(opted_out)
    never_filled = seeded.scalar(
        select(PatientTherapy).where(PatientTherapy.patient_id == "PAT_00006")
    )
    assert never_filled.start_date == DEFAULT_AS_OF - timedelta(days=20)
    assert not seeded.scalars(
        select(MedicationFill).where(MedicationFill.patient_id == "PAT_00006")
    ).all()


def test_hero_hcp_best_content_is_not_cleared(seeded):
    """Scenario 2: every diabetes-outcomes item is either expired or pending MLR."""
    items = seeded.scalars(select(Content).where(Content.topic == "diabetes_outcomes")).all()
    assert {c.mlr_status for c in items} == {MlrStatus.APPROVED, MlrStatus.PENDING}
    for c in items:
        expired = c.expiry_date is not None and c.expiry_date <= DEFAULT_AS_OF
        assert expired or c.mlr_status == MlrStatus.PENDING
    clicks = seeded.scalars(
        select(Interaction).where(
            Interaction.target_id == "HCP_0002", Interaction.outcome == "clicked"
        )
    ).all()
    assert len(clicks) >= 2


def test_users_cover_all_roles(seeded):
    roles = set(seeded.scalars(select(User.role)))
    assert roles == set(Role)
    hero_patient_user = seeded.scalar(select(User).where(User.username == "pat00001"))
    assert hero_patient_user.patient_id == "PAT_00001"


def test_latent_traits_are_isolated_in_sim_table(seeded):
    row = seeded.get(SimLatent, ("PATIENT", "PAT_00001"))
    assert row.traits["archetype"] == "forgetful"
    assert set(next(iter(row.traits["therapies"].values()))) == {
        "next_fill",
        "days_supply",
        "copay",
    }
