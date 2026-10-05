"""Every list of patients is ordered by latest activity, newest first, on the server, with
the patient id as a stable tie-breaker. New, changed and recently active patients surface at
the top; the recommendation work queue keeps its priority ranking."""

from datetime import datetime, timedelta

import pytest
from conftest import (
    ADULT_DOB,
    PASSWORD,
    PATIENT_AGREEMENTS,
    ApiClient,
    as_user,
    auth,
    challenge_of,
    new_session,
    signed_in,
    verify,
)
from sqlalchemy import select

from app import cycle
from app.clinical import activity
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import CareManagerPatient, Consent, Nba, Patient, PatientHcp, User
from app.models.enums import NbaStatus


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=91, n_patients=120, n_hcps=30, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def join(client, email, name="Noor Haddad") -> dict:
    body = {
        "name": name, "email": email, "date_of_birth": ADULT_DOB, "password": PASSWORD,
        "confirm_password": PASSWORD, **PATIENT_AGREEMENTS,
    }  # fmt: skip
    return signed_in(verify(client, challenge_of(client.post("/api/auth/signup", json=body))))


def listed(client, who, **params) -> list[str]:
    response = client.get(
        "/api/patients", params={"limit": 200, **params}, headers=auth(client, who)
    )
    return [p["patient_id"] for p in response.json()["items"]]


def manager_of(db, pid) -> str:
    uid = db.scalar(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    )
    return db.get(User, uid).username


def test_synthetic_activity_comes_from_history_not_generation_time(env):
    _, db = env
    synthetic = db.scalars(select(Patient).where(Patient.origin == "synthetic")).all()
    assert all(p.last_activity_at and p.created_at for p in synthetic)
    assert all(p.created_at <= p.last_activity_at for p in synthetic)


def test_new_patient_appears_first_for_admin_and_their_care_manager(env):
    client, db = env
    pid = join(client, "first@example.org")["user"]["patient_id"]
    assert listed(client, "admin")[0] == pid
    assert listed(client, manager_of(db, pid))[0] == pid


def test_activity_moves_a_patient_to_the_top_and_order_is_descending(env):
    client, db = env
    older = join(client, "older@example.org", "Ana Older")
    newer = join(client, "newer@example.org", "Ben Newer")
    assert listed(client, "admin")[:2] == [newer["user"]["patient_id"], older["user"]["patient_id"]]
    # The older patient does something: they report a condition.
    client.post("/api/me/conditions", json={"condition": "hypertension"}, headers=as_user(older))
    ids = listed(client, "admin")
    assert ids[0] == older["user"]["patient_id"]
    stamps = [db.get(Patient, i).last_activity_at for i in ids]
    assert stamps == sorted(stamps, reverse=True)


def test_ties_are_broken_by_patient_id_and_stable_across_pages(env):
    client, db = env
    tie = datetime(2030, 1, 1, 12, 0)
    ids = sorted(listed(client, "admin")[-4:])
    for pid in ids:
        db.get(Patient, pid).last_activity_at = tie
    db.commit()
    full = listed(client, "admin")
    assert full[:4] == ids  # same moment: ordered by patient id
    pages = listed(client, "admin", limit=2) + listed(client, "admin", limit=2, offset=2)
    assert pages == full[:4]
    assert listed(client, "admin") == full  # stable between refreshes
    for pid in ids:
        db.get(Patient, pid).last_activity_at = tie - timedelta(days=4000)
    db.commit()


def test_search_results_keep_the_same_order(env):
    client, _ = env
    everyone = listed(client, "admin")
    found = listed(client, "admin", q="PAT_")
    assert found == [i for i in everyone if i in set(found)]


def test_hcp_and_assignment_lists_use_the_same_order(env):
    client, db = env
    hcp = db.scalar(select(User).where(User.username == "hcp0001"))
    shared = db.scalars(select(PatientHcp.patient_id).where(PatientHcp.hcp_id == hcp.hcp_id)).all()
    # Two of the HCP's sharing patients; the one with later activity must come first.
    sharing = [
        pid
        for pid in shared
        if db.scalar(
            select(Consent.id).where(
                Consent.patient_id == pid, Consent.purpose == "provider_sharing", Consent.granted
            )
        )
    ]
    if len(sharing) >= 2:
        a, b = sharing[:2]
        db.get(Patient, a).last_activity_at = datetime(2031, 1, 1)
        db.get(Patient, b).last_activity_at = datetime(2031, 1, 2)
        db.commit()
        order = [
            p["patient_id"]
            for p in client.get("/api/me/patients", headers=auth(client, "hcp0001")).json()
        ]
        assert order.index(b) < order.index(a)
    cm = db.scalar(select(User).where(User.username == "cm01"))
    detail = client.get(f"/api/admin/users/{cm.id}", headers=auth(client, "admin")).json()
    panel = [p["patient_id"] for p in detail["patients"]]
    assert panel == [i for i in listed(client, "admin") if i in set(panel)]


def test_the_recommendation_queue_keeps_its_priority_ranking(env):
    client, db = env
    queue = client.get(
        "/api/nba", params={"status": NbaStatus.READY_FOR_REVIEW, "limit": 50},
        headers=auth(client, "admin"),
    ).json()["items"]  # fmt: skip
    priorities = [db.get(Nba, n["id"]).priority for n in queue]
    assert priorities == sorted(priorities, reverse=True)


def test_touch_never_moves_activity_backwards(env):
    _, db = env
    pid = db.scalar(select(Patient.patient_id).where(Patient.origin == "synthetic"))
    patient = db.get(Patient, pid)
    now = patient.last_activity_at
    activity.touch(db, pid, now - timedelta(days=30))
    assert patient.last_activity_at == now
    db.rollback()
