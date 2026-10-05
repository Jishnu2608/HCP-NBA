"""Permanent deletion of patient accounts by an administrator, and signing up again after.

Only patients can be deleted, only by an account holding `user:delete`, only with the email
typed to confirm. The person's own data goes; the audit trail stays without identifying
values; the same email can register again as a new, empty patient.
"""

import json

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
from sqlalchemy import func, or_, select

from app import cycle
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import (
    AuditLog,
    CareManagerPatient,
    CareRequest,
    ConsentRecord,
    Patient,
    PatientCondition,
    PatientTherapy,
    PrivacyRequest,
    User,
)


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=71, n_patients=120, n_hcps=30, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def join(client, email, name="Dara Walsh", country="IE") -> dict:
    body = {
        "name": name, "email": email, "date_of_birth": ADULT_DOB, "password": PASSWORD,
        "confirm_password": PASSWORD, **PATIENT_AGREEMENTS, "country": country, "region": None,
    }  # fmt: skip
    response = client.post("/api/auth/signup", json=body)
    assert response.status_code == 201, response.text
    return signed_in(verify(client, challenge_of(response)))


def delete(client, user_id, email, who="admin", **extra):
    return client.request(
        "DELETE", f"/api/admin/users/{user_id}",
        json={"confirm_email": email, **extra}, headers=auth(client, who),
    )  # fmt: skip


def populated(client, email) -> dict:
    session = join(client, email)
    me = as_user(session)
    client.post("/api/me/conditions", json={"condition": "hypertension"}, headers=me)
    client.post(
        "/api/me/medications",
        json={"name": "losartan", "start_date": "2026-05-01", "ongoing": True},
        headers=me,
    )
    client.post("/api/me/care-requests", json={"reason": "Question about doses"}, headers=me)
    return session


def test_only_an_administrator_deletes_and_only_patients(env):
    client, db = env
    session = join(client, "keep.me@example.org")
    uid = session["user"]["id"]
    for who in ("cm01", "compliance1", "hcp0001", "rep01", "pat00001"):
        assert delete(client, uid, "keep.me@example.org", who=who).status_code == 403
    assert delete(client, uid, "wrong@example.org").status_code == 422
    for username in ("cm01", "hcp0001", "admin"):
        staff = db.scalar(select(User).where(User.username == username))
        response = delete(client, staff.id, staff.email)
        assert response.status_code == 409
    assert db.get(User, uid) is not None


def test_deleting_removes_the_account_and_the_patients_data(env):
    client, db = env
    session = populated(client, "erase.me@example.org")
    uid, pid = session["user"]["id"], session["user"]["patient_id"]
    assert db.scalar(select(AuditLog).where(AuditLog.actor == "erase.me@example.org"))
    response = delete(client, uid, "erase.me@example.org")
    assert response.status_code == 200, response.text
    db.expire_all()
    assert db.get(User, uid) is None and db.get(Patient, pid) is None
    for model in (PatientCondition, PatientTherapy, CareRequest, CareManagerPatient):
        assert (
            db.scalar(select(func.count()).select_from(model).where(model.patient_id == pid)) == 0
        )
    assert (
        db.scalar(
            select(func.count()).select_from(ConsentRecord).where(ConsentRecord.user_id == uid)
        )
        == 0
    )
    listed = client.get("/api/admin/users", params={"q": "erase.me"}, headers=auth(client, "admin"))
    assert listed.json()["items"] == []
    # The audit trail keeps its rows but no longer names the person.
    leaks = db.scalars(
        select(AuditLog).where(
            or_(AuditLog.actor == "erase.me@example.org", AuditLog.entity_id == pid)
        )
    ).all()
    assert leaks == []
    texts = [json.dumps(a.detail or {}) + (a.reason or "") for a in db.scalars(select(AuditLog))]
    assert not any("erase.me@example.org" in t or pid in t for t in texts)
    deleted = db.scalar(select(AuditLog).where(AuditLog.action == "account_deleted"))
    assert deleted.actor == "admin" and "erase.me" not in json.dumps(deleted.detail)
    assert db.scalar(select(AuditLog).where(AuditLog.actor == f"deleted patient #{uid}"))
    # The old session is gone with the account.
    assert client.get("/api/auth/me", headers=as_user(session)).status_code == 401


def test_same_person_can_sign_up_again_and_starts_empty(env):
    client, db = env
    first = populated(client, "again@example.org")
    old_uid, old_pid = first["user"]["id"], first["user"]["patient_id"]
    assert delete(client, old_uid, "again@example.org").status_code == 200
    second = join(client, "again@example.org")
    new_uid, new_pid = second["user"]["id"], second["user"]["patient_id"]
    assert new_uid > old_uid and new_pid != old_pid and new_pid.startswith("PAT_R")
    health = client.get("/api/me/health", headers=as_user(second)).json()
    assert health["conditions"] == health["medications"] == health["requests"] == []
    assert len(health["care_team"]["care_managers"]) == 1 and health["care_team"]["hcps"] == []
    assert db.get(Patient, new_pid).country == "IE"


def test_deletion_completes_the_erasure_request(env):
    client, db = env
    session = join(client, "requested@example.org")
    me, uid = as_user(session), session["user"]["id"]
    made = client.post(
        "/api/privacy/requests", json={"type": "erasure", "details": "Please"}, headers=me
    )
    assert made.status_code == 201, made.text
    rid = made.json()["id"]
    queue = client.get("/api/admin/privacy-requests", headers=auth(client, "admin")).json()
    row = next(r for r in queue["items"] if r["id"] == rid)
    assert row["requester"]["deletable"]
    assert delete(client, uid, "requested@example.org", privacy_request_id=rid).status_code == 200
    db.expire_all()
    request = db.get(PrivacyRequest, rid)
    assert request.status == "completed" and request.user_id is None
    assert request.subject_label == f"deleted patient #{uid}" and request.details is None


def test_demo_patient_account_is_unlinked_but_the_demo_record_stays(env):
    client, db = env
    demo = db.scalar(select(User).where(User.username == "pat00003"))
    pid = demo.patient_id
    assert delete(client, demo.id, demo.email).status_code == 200
    db.expire_all()
    assert db.get(Patient, pid) is not None
    assert db.scalar(
        select(func.count()).select_from(PatientTherapy).where(PatientTherapy.patient_id == pid)
    )
