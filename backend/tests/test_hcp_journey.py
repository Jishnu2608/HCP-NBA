"""The HCP's journey (breakpoints H-01 to H-15 of the live audit).

A real, invited HCP receives routed consultations and sees their clinical context only
while the consultation is theirs; a decline reason stays with the care team; routing never
makes anyone a prescriber; "My patients" is the care team plus sharing consent; specialty
changes never leave unsuitable work with the HCP; and the HCP is part of the engine like any
HCP: approved content, reviewed and sent by their representative, reaches their inbox and
their response is recorded, behind the same MLR, channel and frequency gates.
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
    cookie_header,
    invite,
    login,
    new_session,
    session_of,
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
from app.models import (
    AuditLog,
    CareManagerPatient,
    CareRequest,
    Content,
    Hcp,
    Interaction,
    Nba,
    PatientHcp,
    PatientTherapy,
    RepHcp,
    User,
)
from app.models.enums import NbaStatus, TargetType

ENDO, CARDIO = "Endocrinology", "Cardiovascular Disease"


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=83, n_patients=160, n_hcps=40, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def onboard_hcp(client, email, specialties, *, country="GB", name="Mira Kapoor") -> dict:
    body = {"email": email, "role": "hcp", "specialties": specialties}
    sent = client.post("/api/invitations", json=body, headers=auth(client, "admin"))
    assert sent.status_code == 201, sent.text
    accepted = accept(client, token_of(sent), name=name, country=country, region=None)
    assert accepted.status_code == 201, accepted.text
    return signed_in(verify(client, challenge_of(accepted)))


def join(client, email, name="Ria Test") -> dict:
    body = {
        "name": name, "email": email, "date_of_birth": ADULT_DOB, "password": PASSWORD,
        "confirm_password": PASSWORD, **PATIENT_AGREEMENTS, "country": "GB", "region": None,
    }  # fmt: skip
    return signed_in(verify(client, challenge_of(client.post("/api/auth/signup", json=body))))


def cm_headers(client, db, pid) -> dict:
    uid = db.scalar(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    )
    user = db.get(User, uid)
    if user.source == "seed":
        return auth(client, user.username)
    return cookie_header(nba_session=session_of(login(client, user.email, PASSWORD)))


def patient_with_diabetes(client, db, email) -> dict:
    """A patient with confirmed type 2 diabetes and metformin, and an open consultation."""
    s = join(client, email)
    pid = s["user"]["patient_id"]
    me = as_user(s)
    client.post("/api/me/conditions", json={"condition": "type2_diabetes"}, headers=me)
    med = {
        "name": "metformin", "dose_instructions": "500 mg", "schedule": "daily", "end_date": None,
        "ongoing": True, "start_date": (clock.today() - timedelta(days=40)).isoformat(),
        "last_refill_date": (clock.today() - timedelta(days=5)).isoformat(),
    }  # fmt: skip
    assert client.post("/api/me/medications", json=med, headers=me).status_code == 201
    cm = cm_headers(client, db, pid)
    rec = client.get(f"/api/care/patients/{pid}", headers=cm).json()
    cond, therapy = rec["conditions"][0]["id"], rec["medications"][0]["therapy_id"]
    client.post(f"/api/care/conditions/{cond}/status", json={"status": "confirmed"}, headers=cm)
    client.post(f"/api/care/medications/{therapy}/confirm", json={"days_supply": 30}, headers=cm)
    client.post("/api/me/care-requests", json={"reason": "Dose review please"}, headers=me)
    rid = next(
        r["id"]
        for r in client.get("/api/me/health", headers=me).json()["requests"]
        if r["type"] == "consultation"
    )
    return {"session": s, "me": me, "pid": pid, "cm": cm, "condition": cond,
            "therapy": therapy, "request": rid}  # fmt: skip


def route(client, p, hcp_id, **extra):
    return client.put(
        f"/api/care/patients/{p['pid']}/hcp",
        json={
            "hcp_id": hcp_id,
            "condition_id": p["condition"],
            "request_id": p["request"],
            **extra,
        },
        headers=p["cm"],
    )


@pytest.fixture(scope="module")
def mira(env):
    client, _ = env
    s = onboard_hcp(client, "mira.journey@example.org", [ENDO])
    return {"session": s, "h": as_user(s), "hcp_id": s["user"]["hcp_id"]}


def consultation(client, mira, rid) -> dict | None:
    items = client.get("/api/me/consultations", headers=mira["h"]).json()["items"]
    return next((i for i in items if i["id"] == rid), None)


# --- H-05 / H-15: the residence the HCP gave is their location -----------------------------


def test_invited_hcp_record_carries_the_country_they_gave(env, mira):
    client, db = env
    assert db.get(Hcp, mira["hcp_id"]).country == "GB"
    profile = client.get("/api/me/profile", headers=mira["h"]).json()
    assert profile["location"] == "United Kingdom"
    p = patient_with_diabetes(client, db, "loc.patient@example.org")
    options = client.get(
        f"/api/care/patients/{p['pid']}/hcp-options",
        params={"condition_id": p["condition"]},
        headers=p["cm"],
    ).json()
    first = options["items"][0]
    assert first["hcp_id"] == mira["hcp_id"] and first["same_country"] is True
    assert options["local_match"] is True  # no "no HCP in the United Kingdom" contradiction


# --- H-01 / H-04 / H-02 / H-09 / H-11 / H-12: the consultation loop --------------------------


def test_routing_never_creates_a_prescriber_and_a_decline_stays_with_the_team(env, mira):
    client, db = env
    p = patient_with_diabetes(client, db, "decline.patient@example.org")
    assert route(client, p, mira["hcp_id"]).status_code == 200
    assert db.get(PatientTherapy, p["therapy"]).prescriber_hcp_id is None  # H-04
    assert db.get(PatientHcp, (p["pid"], mira["hcp_id"])) is not None
    # While open, the HCP sees the clinical context (H-01, permitted part).
    open_item = consultation(client, mira, p["request"])
    assert open_item["context_available"] and open_item["group"] == "waiting"
    assert open_item["conditions"] and open_item["medications"]
    declined = client.post(
        f"/api/me/consultations/{p['request']}/respond",
        json={"response": "decline", "note_to_care_team": "On leave; internal: route elsewhere"},
        headers=mira["h"],
    )
    assert declined.status_code == 200
    # H-02: the reason reaches the CM as a care-team note, never the patient.
    cm_view = next(
        r for r in client.get(f"/api/care/patients/{p['pid']}", headers=p["cm"]).json()["requests"]
        if r["id"] == p["request"]
    )  # fmt: skip
    assert any(n["kind"] == "hcp_decline" and "internal" in n["text"] for n in cm_view["notes"])
    patient_view = next(
        r for r in client.get("/api/me/health", headers=p["me"]).json()["requests"]
        if r["id"] == p["request"]
    )  # fmt: skip
    assert "internal" not in str(patient_view) and "On leave" not in str(patient_view)
    assert "will arrange another" in patient_view["resolution"]
    # H-04: the decline released the care-team link routing created; no false prescriber.
    assert db.get(PatientHcp, (p["pid"], mira["hcp_id"])) is None
    assert db.get(PatientTherapy, p["therapy"]).prescriber_hcp_id is None
    # H-11 / H-01: the HCP keeps the history line, without clinical data.
    mine = consultation(client, mira, p["request"])
    assert mine["group"] == "history" and mine["declined_by_you"]
    assert mine["conditions"] == [] and mine["medications"] == []
    # H-09: routing to the same HCP again needs a deliberate confirmation.
    options = client.get(
        f"/api/care/patients/{p['pid']}/hcp-options",
        params={"condition_id": p["condition"], "request_id": p["request"]},
        headers=p["cm"],
    ).json()["items"]
    mira_option = next(o for o in options if o["hcp_id"] == mira["hcp_id"])
    assert mira_option["declined_this_request"] and "On leave" in mira_option["decline_reason"]
    again = route(client, p, mira["hcp_id"])
    assert again.status_code == 409 and code_of(again) == "hcp_declined"
    assert route(client, p, mira["hcp_id"], allow_declined=True).status_code == 200


def test_closed_consultation_ends_clinical_access_and_my_patients_follows_consent(env, mira):
    client, db = env
    p = patient_with_diabetes(client, db, "closed.patient@example.org")
    route(client, p, mira["hcp_id"])
    answered = client.post(
        f"/api/me/consultations/{p['request']}/respond",
        json={"response": "advice", "message": "Increase to 500 mg twice daily."},
        headers=mira["h"],
    )
    assert answered.status_code == 200
    assert consultation(client, mira, p["request"])["group"] == "with_care_manager"
    assert consultation(client, mira, p["request"])["medications"]  # still the HCP's work
    client.patch(
        f"/api/care/requests/{p['request']}",
        json={"status": "closed", "resolution": "Dose increased as advised."},
        headers=p["cm"],
    )
    closed = consultation(client, mira, p["request"])
    assert closed["group"] == "history" and not closed["context_available"]
    assert closed["conditions"] == [] and closed["medications"] == []  # H-01, API level
    # My patients (H-12): on the care team after answering, but only with sharing consent.
    assert all(x["patient_id"] != p["pid"]
               for x in client.get("/api/me/patients", headers=mira["h"]).json())  # fmt: skip
    client.put("/api/me/consents/provider-sharing", json={"granted": True}, headers=p["me"])
    shared = next(
        x for x in client.get("/api/me/patients", headers=mira["h"]).json()
        if x["patient_id"] == p["pid"]
    )  # fmt: skip
    assert [t["drug_name"] for t in shared["therapies"]] == ["metformin"]
    assert shared["therapies"][0]["prescribed_by_you"] is False  # no false attribution
    client.put("/api/me/consents/provider-sharing", json={"granted": False}, headers=p["me"])
    assert all(x["patient_id"] != p["pid"]
               for x in client.get("/api/me/patients", headers=mira["h"]).json())  # fmt: skip


# --- H-08: specialty changes never leave unsuitable work with the HCP -----------------------


def test_specialty_change_returns_unsuitable_open_consultations(env):
    client, db = env
    hcp = onboard_hcp(client, "spec.change@example.org", [ENDO], name="Lee Change")
    h = as_user(hcp)
    p = patient_with_diabetes(client, db, "spec.patient@example.org")
    route(client, p, hcp["user"]["hcp_id"])
    # The HCP asks to become a cardiologist only; the administrator sees the impact first.
    client.post(
        "/api/me/specialty-requests",
        json={"action": "replace", "specialties": [CARDIO], "notes": None},
        headers=h,
    )
    admin = auth(client, "admin")
    pending = next(
        r for r in client.get("/api/admin/specialty-requests", headers=admin).json()["items"]
        if r["hcp_id"] == hcp["user"]["hcp_id"]
    )  # fmt: skip
    assert pending["open_consultations_affected"] == 1
    decided = client.post(
        f"/api/admin/specialty-requests/{pending['id']}/approve",
        json={"notes": None},
        headers=admin,
    )
    assert decided.status_code == 200, decided.text
    r = db.get(CareRequest, p["request"])
    assert r.status == "open" and r.assigned_hcp_id is None  # back with the CM, not closed
    assert "will arrange another" in r.resolution
    # H-14: the decision counts for the HCP until they look at it.
    assert client.get("/api/me/attention", headers=h).json()["specialty_decisions"] == 1
    seen = client.get("/api/me/specialty-requests", headers=h).json()["requests"]
    assert seen[0]["new_decision"] is True
    assert client.get("/api/me/attention", headers=h).json()["specialty_decisions"] == 0


# --- H-03 / H-07: a real HCP in the engine, behind the same gates ---------------------------


def test_real_hcp_receives_approved_content_from_their_rep_and_responds(env, mira):
    client, db = env
    admin = auth(client, "admin")
    rep = db.scalar(select(User).where(User.username == "rep01"))
    current = [x["hcp_id"] for x in client.get(f"/api/admin/users/{rep.id}", headers=admin)
               .json()["hcps"]]  # fmt: skip
    assigned = client.put(
        f"/api/admin/users/{rep.id}/assignments",
        json={"hcp_ids": [*current, mira["hcp_id"]]},
        headers=admin,
    )
    assert assigned.status_code == 200
    pop = load_population(db)
    assert mira["hcp_id"] in pop.hcps
    cycle.run(db)
    db.commit()
    nba = db.scalar(
        select(Nba).where(Nba.target_type == TargetType.HCP, Nba.target_id == mira["hcp_id"])
        .order_by(Nba.id.desc())
    )  # fmt: skip
    assert nba is not None
    assert "None" not in str(nba.rationale)  # an invited HCP's specialty reads properly
    # Nothing is guessed about an invited HCP: no volume tier, no value score.
    hcp = db.get(Hcp, mira["hcp_id"])
    assert hcp.segment.startswith("unrated_") and hcp.value_score is None
    assert "lower-volume" not in str(nba.rationale)
    content = db.get(Content, nba.content_id)
    # Same gates: only approved, in-date content for the HCP audience and their specialty.
    if nba.status == NbaStatus.READY_FOR_REVIEW:
        assert content.mlr_status == "approved" and content.audience == "HCP"
        assert content.specialty in (None, ENDO)
    # Content pending MLR is never the chosen option for anyone.
    from app.models import NbaCandidate

    for c in db.scalars(select(NbaCandidate).where(NbaCandidate.nba_id == nba.id)):
        cand = db.get(Content, c.content_id)
        if cand.mlr_status != "approved":
            assert not c.eligible
    assert nba.status == NbaStatus.READY_FOR_REVIEW, nba.block_reason
    # The representative sees it, approves and sends it.
    rep_h = auth(client, "rep01")
    assert client.get(f"/api/nba/{nba.id}", headers=rep_h).status_code == 200
    assert client.post(f"/api/nba/{nba.id}/approve", json={}, headers=rep_h).status_code == 200
    sent = client.post(f"/api/nba/{nba.id}/send", headers=rep_h)
    assert sent.status_code == 200, sent.text
    interaction = db.scalar(select(Interaction).where(Interaction.nba_id == nba.id))
    if nba.channel in ("portal", "email", "sms"):
        inbox = client.get("/api/me/inbox", headers=mira["h"]).json()
        item = next(i for i in inbox if i["id"] == interaction.id)
        assert item["content_title"] == content.title and item["sent_by"]
        done = client.post(
            f"/api/me/inbox/{interaction.id}/respond", json={"response": "clicked"},
            headers=mira["h"],
        )  # fmt: skip
        assert done.status_code == 200
    else:  # a visit: the representative records how it went
        logged = client.post(
            f"/api/nba/{nba.id}/outcome", json={"outcome": "completed"}, headers=rep_h
        )
        assert logged.status_code == 200
    db.refresh(interaction)
    assert interaction.outcome in ("clicked", "completed")
    # The response is history the next cycle learns from.
    assert any(
        i.id == interaction.id
        for i in load_population(db).interactions[(TargetType.HCP, mira["hcp_id"])]
    )


def test_rep_invited_by_an_hcp_works_with_that_hcp(env, mira):
    client, db = env
    sent = invite(client, mira["h"], "rep.of.mira@example.org", "medical_rep")
    assert sent.status_code == 201, sent.text
    accepted = accept(client, token_of(sent), name="Rep Of Mira")
    s = signed_in(verify(client, challenge_of(accepted)))
    links = db.scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == s["user"]["id"])).all()
    assert mira["hcp_id"] in links


# --- H-10: audit actors are durable accounts ----------------------------------------------


def test_audit_rows_name_the_account_not_just_the_email(env, mira):
    client, db = env
    rows = db.scalars(
        select(AuditLog).where(AuditLog.actor == mira["session"]["user"]["email"])
    ).all()
    assert rows and all(r.actor_user_id == mira["session"]["user"]["id"] for r in rows)
    audit = client.get("/api/audit", params={"limit": 5}, headers=auth(client, "admin")).json()
    assert "actor_user_id" in audit["items"][0]
