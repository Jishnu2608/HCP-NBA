"""Invited HCPs, their specialties and specialty change requests; the real date; medication
dates; the administrator's privacy view.

An invited HCP gets a blank record with exactly the specialties the inviter chose (none =
"not configured"). Only an administrator changes them, directly or by approving the HCP's
own request. Matching for patient routing uses every specialty an HCP holds.
"""

from datetime import timedelta

import pytest
from conftest import (
    ADULT_DOB,
    PASSWORD,
    PATIENT_AGREEMENTS,
    ApiClient,
    accept,
    as_user,
    auth,
    challenge_of,
    new_session,
    signed_in,
    token_of,
    verify,
)
from sqlalchemy import select

from app import cycle
from app.core import clock
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.features.population import load_population
from app.main import app
from app.models import AuditLog, CareManagerPatient, Hcp, HcpSpecialty, User

CARDIO, GENERAL, ENDO = "Cardiovascular Disease", "Internal Medicine", "Endocrinology"


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=81, n_patients=120, n_hcps=30, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def invite_hcp(client, email, specialties=None, by="admin"):
    body = {"email": email, "role": "hcp"}
    if specialties is not None:
        body["specialties"] = specialties
    return client.post("/api/invitations", json=body, headers=auth(client, by))


def onboard_hcp(client, email, specialties=None, name="Jishnudeep Borah") -> dict:
    sent = invite_hcp(client, email, specialties)
    assert sent.status_code == 201, sent.text
    accepted = accept(client, token_of(sent), name=name)
    assert accepted.status_code == 201, accepted.text
    return signed_in(verify(client, challenge_of(accepted)))


def codes(items) -> set[str]:
    return {s["code"] for s in items}


# --- Invited HCPs: blank record, explicit specialties ---------------------------------------


def test_invited_hcp_has_a_blank_record_and_no_assumed_specialty(env):
    client, db = env
    session = onboard_hcp(client, "plain.hcp@example.org")
    hcp = db.get(Hcp, session["user"]["hcp_id"])
    assert hcp.hcp_id.startswith("HCP_R") and hcp.origin == "invited"
    assert (hcp.specialty, hcp.npi, hcp.organization, hcp.city) == (None, None, None, None)
    assert db.scalars(select(HcpSpecialty).where(HcpSpecialty.hcp_id == hcp.hcp_id)).all() == []
    profile = client.get("/api/me/profile", headers=as_user(session)).json()
    assert profile["specialties"] == [] and profile["name"] == "Dr. Jishnudeep Borah"


def test_inviter_sets_several_specialties_and_both_are_shown(env):
    client, db = env
    session = onboard_hcp(client, "two.specs@example.org", [CARDIO, GENERAL])
    profile = client.get("/api/me/profile", headers=as_user(session)).json()
    assert codes(profile["specialties"]) == {CARDIO, GENERAL}
    assert {s["label"] for s in profile["specialties"]} == {
        "Cardiology", "Internal Medicine (general medicine)"
    }  # fmt: skip
    uid = session["user"]["id"]
    detail = client.get(f"/api/admin/users/{uid}", headers=auth(client, "admin")).json()
    assert codes(detail["hcp"]["specialties"]) == {CARDIO, GENERAL}


def test_invitation_specialties_are_validated(env):
    client, _ = env
    assert code_of(invite_hcp(client, "bad.spec@example.org", ["Neurosurgery"])) == (
        "invalid_specialty"
    )
    response = client.post(
        "/api/invitations",
        json={"email": "rep.spec@example.org", "role": "medical_rep", "specialties": [CARDIO]},
        headers=auth(client, "admin"),
    )
    assert response.status_code == 422 and code_of(response) == "invalid_specialty"


def test_invited_hcps_join_the_engine_and_can_be_assigned_a_rep(env):
    """A real (invited) HCP with an active account is part of the engine's HCP population
    and can be assigned to a representative, behind the same gates as a synthetic HCP."""
    client, db = env
    session = onboard_hcp(client, "outside.engine@example.org", [ENDO])
    hid = session["user"]["hcp_id"]
    pop = load_population(db)
    assert hid in pop.hcps and pop.hcp_specialties[hid] == {ENDO}
    rep = db.scalar(select(User).where(User.username == "rep01"))
    current = [h["hcp_id"] for h in client.get(
        f"/api/admin/users/{rep.id}", headers=auth(client, "admin")
    ).json()["hcps"]]  # fmt: skip
    response = client.put(
        f"/api/admin/users/{rep.id}/assignments", json={"hcp_ids": [*current, hid]},
        headers=auth(client, "admin"),
    )  # fmt: skip
    assert response.status_code == 200


# --- Who may change specialties ------------------------------------------------------------


def test_only_an_administrator_edits_specialties(env):
    client, db = env
    session = onboard_hcp(client, "edited@example.org")
    uid = session["user"]["id"]
    for who in ("cm01", "rep01", "compliance1", "pat00001", "hcp0001"):
        response = client.put(
            f"/api/admin/users/{uid}/specialties", json={"specialties": [CARDIO]},
            headers=auth(client, who),
        )  # fmt: skip
        assert response.status_code == 403
    done = client.put(
        f"/api/admin/users/{uid}/specialties", json={"specialties": [CARDIO, ENDO]},
        headers=auth(client, "admin"),
    )  # fmt: skip
    assert done.status_code == 200 and codes(done.json()["hcp"]["specialties"]) == {CARDIO, ENDO}
    change = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "hcp_specialties_changed",
            AuditLog.entity_id == session["user"]["hcp_id"],
        )
    )
    assert change.detail["previous"] == [] and set(change.detail["new"]) == {CARDIO, ENDO}
    # The HCP has no way to set them directly.
    me = as_user(session)
    assert client.put(
        f"/api/admin/users/{uid}/specialties", json={"specialties": []}, headers=me
    ).status_code == 403  # fmt: skip
    assert client.post(
        "/api/me/specialty-requests",
        json={"action": "add", "specialties": [GENERAL], "status": "approved"}, headers=me,
    ).status_code == 422  # fmt: skip


def test_specialty_change_request_lifecycle(env):
    client, db = env
    session = onboard_hcp(client, "asks@example.org", [CARDIO])
    me, hid = as_user(session), session["user"]["hcp_id"]
    made = client.post(
        "/api/me/specialty-requests",
        json={"action": "add", "specialties": [GENERAL], "notes": "Board certified 2019"},
        headers=me,
    )
    assert made.status_code == 201, made.text
    assert made.json()["status"] == "pending"
    assert codes(made.json()["requested"]) == {CARDIO, GENERAL}
    # Nothing changes until an administrator approves.
    profile = client.get("/api/me/profile", headers=me).json()
    assert codes(profile["specialties"]) == {CARDIO}
    # One pending request at a time.
    again = client.post(
        "/api/me/specialty-requests", json={"action": "remove", "specialties": [CARDIO]},
        headers=me,
    )  # fmt: skip
    assert again.status_code == 409 and code_of(again) == "request_pending"
    # A request that would change nothing is refused.
    queue = client.get(
        "/api/admin/specialty-requests", params={"status": "pending"},
        headers=auth(client, "admin"),
    ).json()  # fmt: skip
    rid = next(r["id"] for r in queue["items"] if r["hcp_id"] == hid)
    assert client.post(
        f"/api/admin/specialty-requests/{rid}/approve", json={}, headers=auth(client, "cm01")
    ).status_code == 403  # fmt: skip
    approved = client.post(
        f"/api/admin/specialty-requests/{rid}/approve", json={"notes": "Verified"},
        headers=auth(client, "admin"),
    )  # fmt: skip
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    mine = client.get("/api/me/specialty-requests", headers=me).json()
    assert codes(mine["specialties"]) == {CARDIO, GENERAL}
    assert mine["requests"][0]["status"] == "approved"
    assert client.post(
        f"/api/admin/specialty-requests/{rid}/reject", json={}, headers=auth(client, "admin")
    ).status_code == 409  # fmt: skip

    # Rejection leaves the specialties alone.
    second = client.post(
        "/api/me/specialty-requests", json={"action": "replace", "specialties": [ENDO]},
        headers=me,
    ).json()  # fmt: skip
    rejected = client.post(
        f"/api/admin/specialty-requests/{second['id']}/reject", json={"notes": "No evidence"},
        headers=auth(client, "admin"),
    )  # fmt: skip
    assert rejected.json()["status"] == "rejected"
    assert codes(client.get("/api/me/profile", headers=me).json()["specialties"]) == {
        CARDIO, GENERAL
    }  # fmt: skip
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.entity_id == hid)))
    assert {"specialty_change_requested", "specialty_change_approved",
            "specialty_change_rejected", "hcp_specialties_changed"} <= actions  # fmt: skip


def test_a_stale_request_cannot_overwrite_a_later_change(env):
    client, _ = env
    session = onboard_hcp(client, "stale@example.org", [CARDIO])
    me, uid = as_user(session), session["user"]["id"]
    made = client.post(
        "/api/me/specialty-requests", json={"action": "add", "specialties": [ENDO]}, headers=me
    ).json()
    # The administrator cannot edit directly while a request waits ...
    blocked = client.put(
        f"/api/admin/users/{uid}/specialties", json={"specialties": [GENERAL]},
        headers=auth(client, "admin"),
    )  # fmt: skip
    assert blocked.status_code == 409 and code_of(blocked) == "request_pending"
    # ... and approval re-checks that nothing changed underneath it.
    from app.clinical import hcps

    _, db = env
    db.add(HcpSpecialty(hcp_id=session["user"]["hcp_id"], specialty=GENERAL))
    db.commit()
    stale = client.post(
        f"/api/admin/specialty-requests/{made['id']}/approve", json={},
        headers=auth(client, "admin"),
    )  # fmt: skip
    assert stale.status_code == 409 and code_of(stale) == "request_stale"
    assert set(hcps.specialties_of(db, session["user"]["hcp_id"])) == {CARDIO, GENERAL}


# --- Routing uses every specialty -----------------------------------------------------------


def _patient_with_condition(client, db, email, condition):
    body = {
        "name": "Ravi Menon", "email": email, "date_of_birth": ADULT_DOB, "password": PASSWORD,
        "confirm_password": PASSWORD, **PATIENT_AGREEMENTS,
    }  # fmt: skip
    session = signed_in(verify(client, challenge_of(client.post("/api/auth/signup", json=body))))
    me = as_user(session)
    client.post("/api/me/conditions", json={"condition": condition}, headers=me)
    pid = session["user"]["patient_id"]
    cm_id = db.scalar(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    )
    cm = auth(client, db.get(User, cm_id).username)
    condition_id = client.get(f"/api/care/patients/{pid}", headers=cm).json()["conditions"][0]["id"]
    return pid, cm, condition_id


def test_matching_considers_every_specialty_and_skips_unconfigured_hcps(env):
    client, db = env
    both = onboard_hcp(client, "cardio.general@example.org", [CARDIO, GENERAL])["user"]["hcp_id"]
    none = onboard_hcp(client, "no.spec@example.org")["user"]["hcp_id"]
    pid, cm, cid = _patient_with_condition(client, db, "bp.patient@example.org", "hypertension")
    options = client.get(
        f"/api/care/patients/{pid}/hcp-options", params={"condition_id": cid}, headers=cm
    ).json()
    ids = [o["hcp_id"] for o in options["items"]]
    assert both in ids and none not in ids
    offered = next(o for o in options["items"] if o["hcp_id"] == both)
    assert codes(offered["specialties"]) == {CARDIO, GENERAL}
    assert offered["reason"].startswith("Cardiology")
    assigned = client.put(
        f"/api/care/patients/{pid}/hcp", json={"hcp_id": both, "condition_id": cid}, headers=cm
    )
    assert assigned.status_code == 200, assigned.text
    refused = client.put(
        f"/api/care/patients/{pid}/hcp", json={"hcp_id": none, "condition_id": cid}, headers=cm
    )
    assert refused.status_code == 422 and code_of(refused) == "specialty_mismatch"


# --- Real date, medication dates ------------------------------------------------------------


def test_medication_dates_follow_the_real_date(env):
    client, db = env
    body = {
        "name": "Hana Ito", "email": "dates@example.org", "date_of_birth": ADULT_DOB,
        "password": PASSWORD, "confirm_password": PASSWORD, **PATIENT_AGREEMENTS,
    }  # fmt: skip
    session = signed_in(verify(client, challenge_of(client.post("/api/auth/signup", json=body))))
    me, pid = as_user(session), session["user"]["patient_id"]
    today = clock.today()

    def report(**fields):
        return client.post("/api/me/medications", json={"name": "metformin", **fields}, headers=me)

    assert report(start_date=today.isoformat()).status_code == 422  # ongoing is required
    assert report(start_date=today.isoformat(), ongoing=True).status_code == 201
    tomorrow = (today + timedelta(days=1)).isoformat()
    assert code_of(report(start_date=tomorrow, ongoing=True)) == "invalid_start_date"
    assert code_of(report(start_date="1900-01-01", ongoing=True)) == "invalid_start_date"
    backwards = report(start_date=today.isoformat(), ongoing=False,
                       end_date=(today - timedelta(days=3)).isoformat())  # fmt: skip
    assert code_of(backwards) == "end_before_start"
    assert code_of(report(start_date=today.isoformat(), ongoing=False)) == "invalid_end_date"
    assert code_of(report(start_date=today.isoformat(), ongoing=True, end_date=tomorrow)) == (
        "invalid_end_date"
    )
    # The care team may record a course that starts later and ends on a planned date.
    cm_id = db.scalar(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    )
    cm = auth(client, db.get(User, cm_id).username)
    planned = client.post(
        f"/api/care/patients/{pid}/medications",
        json={"name": "lisinopril", "start_date": tomorrow, "ongoing": False,
              "end_date": (today + timedelta(days=90)).isoformat(), "days_supply": 30},
        headers=cm,
    )  # fmt: skip
    assert planned.status_code == 201, planned.text
    # The patient's own refill action is not available to the care manager.
    tid = planned.json()["medications"][-1]["therapy_id"]
    assert client.post(f"/api/me/medications/{tid}/refill", json={}, headers=cm).status_code == 403


# --- Administrator privacy view --------------------------------------------------------------


def test_administrator_handles_privacy_requests_and_does_not_submit_them(env):
    client, _ = env
    admin = auth(client, "admin")
    own = client.post("/api/privacy/requests", json={"type": "access"}, headers=admin)
    assert own.status_code == 403
    assert client.get("/api/admin/privacy-requests", headers=admin).status_code == 200
    me = client.get("/api/auth/me", headers=admin).json()
    assert "privacy:request" not in me["permissions"] and "privacy:manage" in me["permissions"]
    for who in ("pat00001", "hcp0001", "cm01"):
        made = client.post(
            "/api/privacy/requests", json={"type": "access"}, headers=auth(client, who)
        )
        assert made.status_code == 201, made.text


def test_legacy_invited_hcp_moves_off_the_synthetic_record(env):
    from app.clinical.hcps import separate_real_hcps
    from app.datagen.generate import synthetic_hcp_names

    _, db = env
    synthetic = db.get(Hcp, "HCP_0010")
    expected = synthetic_hcp_names(["HCP_0010"])["HCP_0010"]
    synthetic.first_name, synthetic.last_name = "Legacy", "Doctor"
    user = User(
        username="legacy.hcp@example.org", email="legacy.hcp@example.org",
        display_name="Legacy Doctor", password_hash="x", role="hcp", verified=True,
        status="active", source="invitation", hcp_id="HCP_0010",
    )  # fmt: skip
    db.add(user)
    db.flush()
    assert separate_real_hcps(db) == 1 and separate_real_hcps(db) == 0
    own = db.get(Hcp, user.hcp_id)
    assert own.origin == "invited" and own.specialty is None
    assert db.scalars(select(HcpSpecialty).where(HcpSpecialty.hcp_id == own.hcp_id)).all() == []
    assert (synthetic.first_name, synthetic.last_name) == expected
    db.commit()
