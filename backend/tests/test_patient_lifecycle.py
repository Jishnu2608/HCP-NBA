"""Patient lifecycle and data ownership.

A self-registered patient gets their own empty record and a care manager; clinical data
enters only through the patient's own entries or their care team. A clinic patient's record
is prepared by a care manager and bound to the invitation. Synthetic demo records are never
handed to a real person, and location is what the person chose.
"""

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
    token_of,
    verify,
)
from sqlalchemy import func, select

from app import cycle
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.engagement import simulator
from app.features.population import load_population
from app.main import app
from app.models import (
    CareManagerPatient,
    CareRequest,
    Consent,
    Hcp,
    Interaction,
    MedicationFill,
    Nba,
    Patient,
    PatientHcp,
    PatientTherapy,
    SimLatent,
    User,
)
from app.models.enums import TargetType


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=61, n_patients=160, n_hcps=40, n_reps=4, n_care_managers=3))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def join(client, email, name="Jordan Lee", country="IE", region=None) -> dict:
    body = {
        "name": name, "email": email, "date_of_birth": ADULT_DOB, "password": PASSWORD,
        "confirm_password": PASSWORD, **PATIENT_AGREEMENTS, "country": country, "region": region,
    }  # fmt: skip
    response = client.post("/api/auth/signup", json=body)
    assert response.status_code == 201, response.text
    return signed_in(verify(client, challenge_of(response)))


def manager_of(db, patient_id) -> User:
    uid = db.scalar(
        select(CareManagerPatient.care_manager_user_id).where(
            CareManagerPatient.patient_id == patient_id
        )
    )
    return db.get(User, uid)


def cm_headers(client, db, patient_id) -> dict:
    return auth(client, manager_of(db, patient_id).username)


def count(db, model, **where) -> int:
    query = select(func.count()).select_from(model)
    for column, value in where.items():
        query = query.where(getattr(model, column) == value)
    return db.scalar(query)


# --- Path A: a new patient starts clean -----------------------------------------------------


def test_new_patient_starts_with_a_blank_clinical_profile(env):
    client, db = env
    synthetic_names = {p.patient_id: p.first_name for p in db.scalars(select(Patient))}
    session = join(client, "blank.patient@example.org", "Aoife Byrne")
    pid = session["user"]["patient_id"]
    patient = db.get(Patient, pid)
    assert pid.startswith("PAT_R") and patient.origin == "self_registered"
    for model in (PatientTherapy, MedicationFill, Consent, CareRequest):
        assert count(db, model, patient_id=pid) == 0
    assert count(db, Nba, target_id=pid) == count(db, Interaction, target_id=pid) == 0
    assert db.get(SimLatent, (TargetType.PATIENT, pid)) is None
    # No synthetic record was renamed or handed over.
    assert all(db.get(Patient, p).first_name == name for p, name in synthetic_names.items())
    health = client.get("/api/me/health", headers=as_user(session)).json()
    assert health["conditions"] == health["medications"] == health["requests"] == []
    assert len(health["care_team"]["care_managers"]) == 1
    assert health["care_team"]["hcps"] == []


def test_care_manager_is_assigned_to_the_least_loaded(env):
    client, db = env
    first = join(client, "least.one@example.org")["user"]["patient_id"]
    second = join(client, "least.two@example.org")["user"]["patient_id"]
    assert manager_of(db, first).id != manager_of(db, second).id
    # The assignment created no clinical data.
    assert count(db, PatientTherapy, patient_id=second) == 0


def test_location_is_what_the_patient_chose(env):
    client, db = env
    ireland = join(client, "yaswanth@example.org", "Yaswanth Reddy", country="IE")
    texas = join(client, "tx.person@example.org", "Sam Ortiz", country="US", region="TX")
    for session, expected in ((ireland, "Ireland"), (texas, "Texas, United States")):
        pid = session["user"]["patient_id"]
        patient = db.get(Patient, pid)
        assert patient.city is None and patient.state is None  # nothing invented
        assert (
            client.get("/api/me/profile", headers=as_user(session)).json()["location"] == expected
        )
        admin = auth(client, "admin")
        assert client.get(f"/api/patients/{pid}", headers=admin).json()["location"] == expected
        listed = client.get("/api/patients", params={"q": pid}, headers=admin).json()["items"]
        assert [p["location"] for p in listed] == [expected]
        uid = session["user"]["id"]
        assert client.get(f"/api/admin/users/{uid}", headers=admin).json()["location"] == expected
    assert "Houston" not in str(client.get("/api/me/profile", headers=as_user(ireland)).json())


def test_patient_adds_condition_medication_and_asks_to_consult(env):
    client, db = env
    session = join(client, "builder@example.org", "Mei Tan")
    me, pid = as_user(session), session["user"]["patient_id"]
    added = client.post("/api/me/conditions", json={"condition": "type2_diabetes"}, headers=me)
    assert added.status_code == 201, added.text
    med = client.post(
        "/api/me/medications",
        json={"name": "Metformin", "dose_instructions": "500 mg with dinner",
              "schedule": "Once daily", "start_date": "2026-08-01", "ongoing": True},
        headers=me,
    )  # fmt: skip
    assert med.status_code == 201, med.text
    consult = client.post("/api/me/care-requests", json={"reason": "Feeling dizzy"}, headers=me)
    assert consult.status_code == 201
    health = consult.json()
    assert [c["status"] for c in health["conditions"]] == ["reported"]
    assert health["medications"][0]["review_status"] == "reported"
    assert health["medications"][0]["measure"] == "diabetes"
    assert sorted(r["type"] for r in health["requests"]) == [
        "condition_review", "consultation", "medication_review",
    ]  # fmt: skip
    # The care manager sees them in their queue.
    queue = client.get("/api/care/requests", headers=cm_headers(client, db, pid)).json()
    assert {r["patient_id"] for r in queue["items"]} >= {pid}
    # A reported medication counts for nothing in the engine.
    assert pid not in load_population(db).therapies


def test_patient_cannot_forge_fields(env):
    client, _ = env
    me = as_user(join(client, "forger@example.org"))
    for path, body in (
        ("/api/me/conditions", {"condition": "hypertension", "status": "confirmed"}),
        ("/api/me/conditions", {"condition": "hypertension", "patient_id": "PAT_00001"}),
        (
            "/api/me/medications",
            {
                "name": "x",
                "start_date": "2026-01-01",
                "ongoing": True,
                "review_status": "confirmed",
            },
        ),
        ("/api/me/care-requests", {"reason": "x", "assigned_hcp_id": "HCP_0001"}),
    ):
        assert client.post(path, json=body, headers=me).status_code == 422


def test_demo_patients_records_are_read_only(env):
    client, _ = env
    response = client.post(
        "/api/me/conditions", json={"condition": "hypertension"}, headers=auth(client, "pat00001")
    )
    assert response.status_code == 409 and code_of(response) == "demo_record"


# --- Care manager: confirm, route to an HCP ----------------------------------------------------


def test_care_manager_confirms_routes_and_patient_sees_the_hcp(env):
    client, db = env
    session = join(client, "routed@example.org", "Lena Fischer")
    me, pid = as_user(session), session["user"]["patient_id"]
    client.post("/api/me/conditions", json={"condition": "type2_diabetes"}, headers=me)
    client.post(
        "/api/me/medications",
        json={"name": "metformin", "start_date": "2026-07-01", "ongoing": True},
        headers=me,
    )
    cm = cm_headers(client, db, pid)
    record = client.get(f"/api/care/patients/{pid}", headers=cm).json()
    condition = record["conditions"][0]
    assert (
        client.post(
            f"/api/care/conditions/{condition['id']}/status",
            json={"status": "confirmed"},
            headers=cm,
        ).status_code
        == 200
    )

    options = client.get(
        f"/api/care/patients/{pid}/hcp-options",
        params={"condition_id": condition["id"]},
        headers=cm,
    ).json()
    suited = {"Endocrinology", "Family Medicine", "Internal Medicine"}
    held = [{s["code"] for s in o["specialties"]} for o in options["items"]]
    assert held and all(h & suited for h in held)
    assert "Endocrinology" in held[0]  # the specialist comes first
    # No HCP was linked by looking.
    assert count(db, PatientHcp, patient_id=pid) == 0

    cardiologist = db.scalar(select(Hcp).where(Hcp.specialty == "Cardiovascular Disease"))
    wrong = client.put(
        f"/api/care/patients/{pid}/hcp",
        json={"hcp_id": cardiologist.hcp_id, "condition_id": condition["id"]}, headers=cm,
    )  # fmt: skip
    assert wrong.status_code == 422 and code_of(wrong) == "specialty_mismatch"
    endo = options["items"][0]["hcp_id"]
    assigned = client.put(
        f"/api/care/patients/{pid}/hcp", json={"hcp_id": endo, "condition_id": condition["id"]},
        headers=cm,
    )  # fmt: skip
    assert assigned.status_code == 200, assigned.text

    therapy = record["medications"][0]
    confirmed = client.post(
        f"/api/care/medications/{therapy['therapy_id']}/confirm",
        json={"days_supply": 30, "copay": 5}, headers=cm,
    )  # fmt: skip
    assert confirmed.status_code == 200, confirmed.text
    health = client.get("/api/me/health", headers=me).json()
    assert [h["hcp_id"] for h in health["care_team"]["hcps"]] == [endo]
    assert health["medications"][0]["review_status"] == "confirmed"
    assert health["medications"][0]["prescriber"]  # the routed HCP prescribes it now
    assert all(r["status"] == "closed" for r in health["requests"])
    # Confirmed: now part of what the engine sees. The patient can log a refill.
    assert pid in load_population(db).therapies
    refill = client.post(f"/api/me/medications/{therapy['therapy_id']}/refill", json={}, headers=me)
    assert refill.status_code == 201, refill.text
    fill = db.scalar(select(MedicationFill).where(MedicationFill.patient_id == pid))
    assert fill.source == "patient"


def test_care_manager_outside_the_panel_cannot_touch_the_patient(env):
    client, db = env
    pid = join(client, "other.panel@example.org")["user"]["patient_id"]
    owner = manager_of(db, pid)
    other = db.scalar(
        select(User).where(User.username.like("cm%"), User.id != owner.id).order_by(User.id)
    )
    headers = auth(client, other.username)
    assert client.get(f"/api/care/patients/{pid}", headers=headers).status_code == 404
    assert (
        client.post(
            f"/api/care/patients/{pid}/conditions",
            json={"condition": "hypertension"},
            headers=headers,
        ).status_code
        == 404
    )
    for who in ("rep01", "compliance1", "hcp0001", "pat00001", "admin"):
        assert client.get(f"/api/care/patients/{pid}", headers=auth(client, who)).status_code == 403


# --- Path B: clinic patient, invited by the care manager ---------------------------------------


def test_clinic_patient_is_invited_and_sees_their_existing_record(env):
    client, db = env
    cm = auth(client, "cm02")
    created = client.post(
        "/api/care/patients",
        json={"name": "Grace Kim", "date_of_birth": "1970-03-04", "country": "US", "region": "NY"},
        headers=cm,
    )
    assert created.status_code == 201, created.text
    pid = created.json()["patient_id"]
    assert db.get(Patient, pid).origin == "clinic"
    client.post(
        f"/api/care/patients/{pid}/conditions", json={"condition": "hypertension"}, headers=cm
    )
    client.post(
        f"/api/care/patients/{pid}/medications",
        json={"name": "lisinopril", "start_date": "2026-06-01", "days_supply": 90, "ongoing": True},
        headers=cm,
    )
    client.post(
        f"/api/care/patients/{pid}/notes",
        json={"kind": "hcp_instruction", "text": "Check blood pressure twice a week."},
        headers=cm,
    )
    # Only from the record, and only by the responsible care manager.
    assert (
        client.post(
            "/api/invitations", json={"email": "grace@example.org", "role": "patient"}, headers=cm
        ).status_code
        == 422
    )
    assert client.post(
        f"/api/care/patients/{pid}/invite", json={"email": "grace@example.org"},
        headers=auth(client, "cm01"),
    ).status_code == 404  # fmt: skip
    sent = client.post(
        f"/api/care/patients/{pid}/invite", json={"email": "grace@example.org"}, headers=cm
    )
    assert sent.status_code == 201, sent.text
    token = token_of(sent)
    lookup = client.post("/api/invitations/lookup", json={"token": token}).json()
    assert lookup["patient"] and lookup["role"] == "patient"

    body = {
        "token": token, "name": "Grace Kim", "date_of_birth": "1970-03-04",
        "password": PASSWORD, "confirm_password": PASSWORD, **PATIENT_AGREEMENTS,
        "country": "US", "region": "NY",
    }  # fmt: skip
    missing_consent = client.post(
        "/api/invitations/accept", json={**body, "consent_health_data": False}
    )
    assert code_of(missing_consent) == "health_consent_required"
    accepted = client.post("/api/invitations/accept", json=body)
    assert accepted.status_code == 201, accepted.text
    session = signed_in(verify(client, challenge_of(accepted)))
    assert session["user"]["patient_id"] == pid
    assert not session["user"]["professionally_verified"]
    health = client.get("/api/me/health", headers=as_user(session)).json()
    assert [m["drug_name"] for m in health["medications"]] == ["lisinopril"]
    assert [c["condition"] for c in health["conditions"]] == ["hypertension"]
    assert health["notes"][0]["text"].startswith("Check blood pressure")
    # The care manager who set it up stays responsible.
    assert manager_of(db, pid).username == "cm02"
    assert client.get(f"/api/care/patients/{pid}", headers=cm).json()["portal"]["state"] == "active"


def test_only_care_managers_invite_patients(env):
    client, _ = env
    for who in ("admin", "hcp0001", "rep01", "pat00001"):
        response = client.post(
            "/api/invitations", json={"email": "x.patient@example.org", "role": "patient"},
            headers=auth(client, who),
        )  # fmt: skip
        assert response.status_code == 403


# --- Engine and simulator -----------------------------------------------------------------------


def test_real_patients_are_never_simulated(env):
    client, db = env
    pid = join(client, "not.simulated@example.org")["user"]["patient_id"]
    db.add(
        Interaction(
            target_type=TargetType.PATIENT, target_id=pid, channel="email",
            int_ts=db.scalar(select(func.max(Interaction.int_ts))), type="education",
            outcome="pending", source="nba",
        )
    )  # fmt: skip
    db.commit()
    simulator.play_out(db, retrain=False)
    db.commit()
    still = db.scalar(select(Interaction).where(Interaction.target_id == pid))
    assert still.outcome == "pending"  # only the person can answer it


# --- Reset ---------------------------------------------------------------------------------------


def test_real_patients_survive_a_demo_reset(env):
    client, db = env
    session = join(client, "survivor@example.org", "Ines Duarte", country="PT")
    pid = session["user"]["patient_id"]
    client.post(
        "/api/me/conditions", json={"condition": "high_cholesterol"}, headers=as_user(session)
    )
    reset = client.post(
        "/api/admin/reset", json={"patients": 160, "hcps": 40}, headers=auth(client, "admin")
    )
    assert reset.status_code == 200, reset.text
    client.__dict__["_tokens"].clear()
    patient = db.get(Patient, pid)
    assert patient is not None and patient.country == "PT"
    assert db.scalar(select(User).where(User.email == "survivor@example.org")).patient_id == pid
    assert count(db, CareRequest, patient_id=pid) == 1
    assert manager_of(db, pid) is not None


# --- Accounts registered before records were separated ------------------------------------------


def test_legacy_account_on_a_synthetic_record_gets_its_own_record(env):
    from app.clinical.records import separate_real_patients
    from app.datagen.generate import synthetic_patient_names

    _, db = env
    synthetic = db.scalar(
        select(Patient)
        .where(Patient.origin == "synthetic", Patient.patient_id.not_in(
            select(User.patient_id).where(User.patient_id.is_not(None))
        ))
        .order_by(Patient.patient_id.desc())
    )  # fmt: skip
    expected = synthetic_patient_names(
        db.scalar(select(func.count()).select_from(Hcp)), [synthetic.patient_id]
    )[synthetic.patient_id]
    synthetic.first_name, synthetic.last_name = "Legacy", "Holder"
    user = User(
        username="legacy@example.org", email="legacy@example.org", display_name="Legacy Holder",
        password_hash="x", role="patient", verified=True, status="active", source="signup",
        patient_id=synthetic.patient_id, country="IE", date_of_birth=None,
    )  # fmt: skip
    db.add(user)
    db.flush()
    assert separate_real_patients(db) == 1
    assert separate_real_patients(db) == 0  # idempotent
    own = db.get(Patient, user.patient_id)
    assert own.origin == "self_registered" and own.country == "IE"
    assert (synthetic.first_name, synthetic.last_name) == expected
    assert db.scalar(select(func.count()).select_from(PatientTherapy).where(
        PatientTherapy.patient_id == own.patient_id)) == 0  # fmt: skip
    db.commit()
