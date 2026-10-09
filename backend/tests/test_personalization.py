"""Approved wording is a template: placeholders are filled for each recipient, from their own
record, by one function; a recipient's view shows their version; template views keep the
fields; reviewers see sent wording with the recipient taken out again."""

import pytest
from conftest import ApiClient, auth, new_session
from sqlalchemy import select

from app import cycle
from app.content import governance
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.llm.template import deidentify, personalize
from app.main import app
from app.models import Content, Interaction, MessageDraft, Nba, Patient, PatientTherapy
from app.models.enums import TargetType


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=73, n_patients=160, n_hcps=40, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def test_fields_are_filled_whatever_their_spacing_or_case():
    text = "Hi {{first_name}}, your {{ Drug_Name }} refill is due. {{ first_name }}"
    assert personalize(text, "Asha", "Metformin") == "Hi Asha, your Metformin refill is due. Asha"
    # A message about no particular medicine still reads naturally.
    assert personalize("Your {{ drug_name }}", "Asha", None) == "Your medication"
    # An unknown field is left for the validator to refuse, never silently blanked.
    assert "{{ dose }}" in personalize("Take {{ dose }}", "Asha", "Metformin")


def test_deidentify_takes_the_recipient_out_again():
    sent = "Hi Asha, your metformin refill. Call guide: Asha Verma"
    out = deidentify(sent, "Asha", "Verma", "Metformin")
    assert "Asha" not in out and "Verma" not in out and "metformin" not in out
    expected = "Hi {{ first_name }}, your {{ drug_name }} refill. Call guide: "
    assert out == expected + "{{ first_name }} {{ last_name }}"


def _templated_patient_nba(db) -> Nba:
    templated = select(Content.content_id).where(Content.body.like("%{{ drug_name }}%"))
    nba = db.scalar(
        select(Nba).where(
            Nba.target_type == TargetType.PATIENT,
            Nba.content_id.in_(templated),
            Nba.therapy_id.is_not(None),
            Nba.id.in_(select(MessageDraft.nba_id)),
        )
    )
    assert nba is not None, "seed data should have a recommendation using templated content"
    return nba


def test_recipient_view_is_filled_from_that_patients_record(env):
    client, db = env
    nba = _templated_patient_nba(db)
    patient = db.get(Patient, nba.target_id)
    drug = db.get(PatientTherapy, nba.therapy_id).drug_name
    data = client.get(f"/api/nba/{nba.id}", headers=auth(client, "admin")).json()
    filled = data["content"]["body_for_recipient"]
    assert "{{" not in filled
    assert patient.first_name in filled and drug in filled
    # The template itself is unchanged in the same payload.
    assert "{{" in data["content"]["body"]
    # Every draft is already filled for this patient.
    for d in data["drafts"]:
        assert "{{" not in d["body"] and "{{" not in (d["subject"] or "")


def test_reviewers_see_sent_wording_without_the_recipient(env):
    client, db = env
    nba = _templated_patient_nba(db)
    patient = db.get(Patient, nba.target_id)
    drug = db.get(PatientTherapy, nba.therapy_id).drug_name
    draft = db.scalar(select(MessageDraft).where(MessageDraft.nba_id == nba.id))
    content = db.get(Content, nba.content_id)
    sent = Interaction(
        target_type=TargetType.PATIENT, target_id=patient.patient_id, channel=nba.channel,
        int_ts=nba.created_ts, type=nba.action, outcome="pending", content_id=content.content_id,
        therapy_id=nba.therapy_id, nba_id=nba.id, source="nba", draft_id=draft.id,
    )  # fmt: skip
    db.add(sent)
    db.flush()
    report = governance.deliveries(db, content.lineage_id or content.content_id)
    text = str(report["wording"])
    assert patient.first_name not in text and drug not in text
    assert "{{ first_name }}" in text
    db.rollback()
