"""The care manager's journey (breakpoints CM-01 to CM-19 of the live audit).

A care manager, seeded or newly invited, must never be left with hidden, orphaned, stale or
silently abandoned work: duplicate entries are refused, a request closes only with an
outcome and never over an unresolved entry, consultations are not taken from the HCP
silently, ownership and attribution move with the patient, adherence and safeguards are
current when read, responses reach the care manager, and every count agrees.
"""

from datetime import timedelta

import pytest
from conftest import (
    ADULT_DOB,
    PASSWORD,
    PATIENT_AGREEMENTS,
    ApiClient,
    as_user,
    auth,
    challenge_of,
    cookie_header,
    login,
    new_session,
    onboard,
    session_of,
    signed_in,
    verify,
)
from sqlalchemy import select

from app import cycle, pipeline
from app.auth.provisioning import assignments
from app.core import clock
from app.core.config import get_settings
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import (
    AdherenceSnapshot,
    AuditLog,
    CareManagerPatient,
    CareRequest,
    Content,
    Interaction,
    MedicationFill,
    Nba,
    Patient,
    User,
)
from app.models.enums import NbaStatus, TargetType
from app.models.tables import utcnow


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=79, n_patients=180, n_hcps=40, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def join(client, email, name="Ria Test") -> dict:
    body = {
        "name": name, "email": email, "date_of_birth": ADULT_DOB, "password": PASSWORD,
        "confirm_password": PASSWORD, **PATIENT_AGREEMENTS, "country": "GB", "region": None,
    }  # fmt: skip
    return signed_in(verify(client, challenge_of(client.post("/api/auth/signup", json=body))))


def cm_of(db, pid) -> User:
    uid = db.scalar(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    )
    return db.get(User, uid)


def headers_of(client, user: User) -> dict:
    """A care manager's session: seeded accounts use the demo password, invited ones the
    password they chose when accepting."""
    if user.source == "seed":
        return auth(client, user.username)
    cache = client.__dict__.setdefault("_invited", {})
    if user.email not in cache:
        cache[user.email] = session_of(login(client, user.email, PASSWORD))
    return cookie_header(nba_session=cache[user.email])


def record(client, cm, pid) -> dict:
    return client.get(f"/api/care/patients/{pid}", headers=cm).json()


def health(client, session) -> dict:
    return client.get("/api/me/health", headers=as_user(session)).json()


def attention(client, headers) -> dict:
    return client.get("/api/me/attention", headers=headers).json()


MED = {"dose_instructions": "10 mg daily", "schedule": "once daily", "end_date": None,
       "ongoing": True}  # fmt: skip


def patient_with_medication(client, db, email, *, last_refill_days=10) -> dict:
    s = join(client, email)
    pid = s["user"]["patient_id"]
    me = as_user(s)
    client.post("/api/me/conditions", json={"condition": "hypertension"}, headers=me)
    body = {
        **MED, "name": "lisinopril",
        "start_date": (clock.today() - timedelta(days=60)).isoformat(),
        "last_refill_date": (clock.today() - timedelta(days=last_refill_days)).isoformat(),
    }  # fmt: skip
    assert client.post("/api/me/medications", json=body, headers=me).status_code == 201
    cm = headers_of(client, cm_of(db, pid))
    rec = record(client, cm, pid)
    cond, therapy = rec["conditions"][0]["id"], rec["medications"][0]["therapy_id"]
    return {"session": s, "pid": pid, "cm": cm, "condition": cond, "therapy": therapy}


def confirm_all(client, p) -> None:
    cm = p["cm"]
    client.post(
        f"/api/care/conditions/{p['condition']}/status", json={"status": "confirmed"}, headers=cm
    )
    done = client.post(
        f"/api/care/medications/{p['therapy']}/confirm", json={"days_supply": 30}, headers=cm
    )
    assert done.status_code == 200, done.text


# --- CM-01: no duplicate condition through the care manager --------------------------------


def test_cm_cannot_record_a_condition_already_on_the_record(env):
    client, db = env
    p = patient_with_medication(client, db, "dup.cm@example.org")
    cm, pid = p["cm"], p["pid"]
    # Still only reported by the patient: the CM is told to confirm or dismiss it instead.
    first = client.post(
        f"/api/care/patients/{pid}/conditions", json={"condition": "hypertension"}, headers=cm
    )
    assert first.status_code == 409 and code_of(first) == "duplicate_condition"
    confirm_all(client, p)
    again = client.post(
        f"/api/care/patients/{pid}/conditions", json={"condition": "hypertension"}, headers=cm
    )
    assert again.status_code == 409 and code_of(again) == "duplicate_condition"
    conditions = [
        c for c in record(client, cm, pid)["conditions"] if c["condition"] == "hypertension"
    ]
    assert len(conditions) == 1
    # A different condition is still recorded normally.
    ok = client.post(
        f"/api/care/patients/{pid}/conditions", json={"condition": "high_cholesterol"}, headers=cm
    )
    assert ok.status_code == 201


# --- CM-02 / CM-03 / CM-05: closing never hides work ------------------------------------------


def test_review_request_cannot_close_over_an_unresolved_entry(env):
    client, db = env
    p = patient_with_medication(client, db, "orphan.cm@example.org")
    cm, pid = p["cm"], p["pid"]
    reviews = [
        r for r in client.get("/api/care/requests", headers=cm).json()["items"]
        if r["patient_id"] == pid and r["type"] in ("condition_review", "medication_review")
    ]  # fmt: skip
    assert {r["type"] for r in reviews} == {"condition_review", "medication_review"}
    for r in reviews:
        refused = client.patch(
            f"/api/care/requests/{r['id']}",
            json={"status": "closed", "resolution": "Looked at it."},
            headers=cm,
        )
        assert refused.status_code == 409 and code_of(refused) == "entry_unresolved"
        assert db.get(CareRequest, r["id"]).status == "open"
    # Confirming the entries is what closes them.
    confirm_all(client, p)
    assert all(db.get(CareRequest, r["id"]).status == "closed" for r in reviews)


def test_closing_requires_an_outcome(env):
    client, db = env
    p = patient_with_medication(client, db, "outcome.cm@example.org")
    confirm_all(client, p)
    s, cm = as_user(p["session"]), p["cm"]
    consult = client.post("/api/me/care-requests", json={"reason": "Headaches"}, headers=s)
    rid = consult.json()["id"] if "id" in consult.json() else None
    rid = rid or next(
        r["id"] for r in health(client, p["session"])["requests"] if r["type"] == "consultation"
    )
    for empty in (None, "", "   "):
        body = {"status": "closed"} | ({} if empty is None else {"resolution": empty})
        refused = client.patch(f"/api/care/requests/{rid}", json=body, headers=cm)
        assert refused.status_code == 422 and code_of(refused) == "invalid_outcome"
        assert db.get(CareRequest, rid).status == "open"
    closed = client.patch(
        f"/api/care/requests/{rid}",
        json={"status": "closed", "resolution": "Booked a GP review for Monday."},
        headers=cm,
    )
    assert closed.status_code == 200
    mine = next(r for r in health(client, p["session"])["requests"] if r["id"] == rid)
    assert mine["status"] == "closed" and mine["resolution"] == "Booked a GP review for Monday."


def test_a_consultation_with_the_hcp_is_withdrawn_openly_never_silently(env):
    client, db = env
    p = patient_with_medication(client, db, "withdraw.cm@example.org")
    confirm_all(client, p)
    s, cm, pid = as_user(p["session"]), p["cm"], p["pid"]
    client.post("/api/me/care-requests", json={"reason": "Dizzy spells"}, headers=s)
    rid = next(
        r["id"] for r in health(client, p["session"])["requests"] if r["type"] == "consultation"
    )
    # The seeded HCP with a portal account answers in the app; routed without a condition.
    hcp_id = db.scalar(select(User.hcp_id).where(User.username == "hcp0001"))
    routed = client.put(
        f"/api/care/patients/{pid}/hcp", json={"hcp_id": hcp_id, "request_id": rid}, headers=cm
    )
    assert routed.status_code == 200, routed.text
    silent = client.patch(f"/api/care/requests/{rid}", json={"status": "closed"}, headers=cm)
    assert silent.status_code == 422 and db.get(CareRequest, rid).status == "awaiting_hcp"
    withdrawn = client.patch(
        f"/api/care/requests/{rid}",
        json={"status": "closed", "resolution": "Seen by her GP in person instead."},
        headers=cm,
    )
    assert withdrawn.status_code == 200
    r = db.get(CareRequest, rid)
    assert r.status == "closed" and r.resolution.startswith("Withdrawn by the care manager")
    # The HCP still sees it, saying plainly that it was withdrawn; so does the patient.
    hcp_user = db.scalar(select(User).where(User.hcp_id == hcp_id))
    theirs = client.get("/api/me/consultations", headers=auth(client, hcp_user.username)).json()
    item = next(i for i in theirs["items"] if i["id"] == rid)
    assert item["status_label"] == "Withdrawn by the care manager"
    mine = next(i for i in health(client, p["session"])["requests"] if i["id"] == rid)
    assert mine["status_label"] == "Withdrawn by the care manager"
    assert db.scalar(
        select(AuditLog).where(
            AuditLog.action == "consultation_withdrawn", AuditLog.entity_id == pid
        )
    )


# --- CM-06: the patient keeps the reason ---------------------------------------------------


def test_dismissal_reason_survives_without_an_open_request(env):
    client, db = env
    p = patient_with_medication(client, db, "reason.cm@example.org")
    cm = p["cm"]
    # An entry whose review request was already closed (data from before the guard).
    for r in db.scalars(select(CareRequest).where(CareRequest.condition_id == p["condition"])):
        r.status, r.resolution = "closed", "Closed earlier."
    db.commit()
    dismissed = client.post(
        f"/api/care/conditions/{p['condition']}/status",
        json={"status": "dismissed", "reason": "Already recorded by your GP."},
        headers=cm,
    )
    assert dismissed.status_code == 200
    mine = next(c for c in health(client, p["session"])["conditions"] if c["id"] == p["condition"])
    assert (
        mine["status"] == "dismissed" and mine["dismissed_reason"] == "Already recorded by your GP."
    )
    dismissed = client.post(
        f"/api/care/medications/{p['therapy']}/dismiss",
        json={"reason": "Entered twice."},
        headers=cm,
    )
    assert dismissed.status_code == 200, dismissed.text
    med = next(
        m for m in health(client, p["session"])["medications"] if m["therapy_id"] == p["therapy"]
    )
    assert med["dismissed_reason"] == "Entered twice."


# --- CM-07 / CM-09 / CM-10: reassignment is visible, audited and moves open work --------------


def test_reassignment_is_audited_per_patient_and_moves_open_work(env):
    client, db = env
    admin = auth(client, "admin")
    p = patient_with_medication(client, db, "move.cm@example.org")
    confirm_all(client, p)
    pid = p["pid"]
    client.post(
        "/api/me/care-requests", json={"reason": "Please call"}, headers=as_user(p["session"])
    )
    rid = next(
        r["id"] for r in health(client, p["session"])["requests"] if r["type"] == "consultation"
    )
    client.patch(f"/api/care/requests/{rid}", json={"status": "in_progress"}, headers=p["cm"])
    old = cm_of(db, pid)
    assert db.get(CareRequest, rid).handled_by_user_id == old.id
    # A newly invited care manager takes the patient over.
    invited = onboard(client, "newcm.move@example.org", "care_manager", name="Ana Newcm")
    new = db.get(User, invited["user"]["id"])
    # The administrator sees whom the patient moves from before saving.
    found = client.get("/api/patients", params={"q": pid}, headers=admin).json()["items"]
    assert found[0]["care_managers"] == [{"id": old.id, "name": old.display_name}]
    panel = client.get(f"/api/admin/users/{new.id}", headers=admin).json()["patients"]
    moved = client.put(
        f"/api/admin/users/{new.id}/assignments",
        json={"patient_ids": [x["patient_id"] for x in panel] + [pid]},
        headers=admin,
    )
    assert moved.status_code == 200
    assert cm_of(db, pid).id == new.id
    assert db.get(CareRequest, rid).handled_by_user_id == new.id
    row = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "care_manager_reassigned", AuditLog.entity_id == pid
        )
    )
    assert row is not None and row.actor == "admin"
    assert row.detail["from"] == old.id and row.detail["to"] == new.id
    # The old care manager no longer sees the patient; the new one sees the open request.
    assert client.get(f"/api/patients/{pid}", headers=auth(client, old.username)).status_code == 404
    new_cm = as_user(invited)
    items = client.get("/api/care/requests", headers=new_cm).json()["items"]
    assert any(r["id"] == rid and r["handled_by"] == "Ana Newcm" for r in items)
    # A care manager's own patient search shows no ownership details.
    own = client.get("/api/patients", params={"q": pid}, headers=new_cm).json()["items"]
    assert own[0]["care_managers"] is None


# --- CM-08: successive care managers start on different records ----------------------------


def test_invited_care_managers_start_with_different_panels(env):
    client, db = env
    a = onboard(client, "panel.a@example.org", "care_manager", name="Panel A")
    b = onboard(client, "panel.b@example.org", "care_manager", name="Panel B")
    pa = set(assignments.patient_ids(db, db.get(User, a["user"]["id"])))
    pb = set(assignments.patient_ids(db, db.get(User, b["user"]["id"])))
    synthetic = {pid for pid in pa | pb if db.get(Patient, pid).origin == "synthetic"}
    assert len(pa & synthetic) >= 20 and len(pb & synthetic) >= 20
    assert not (pa & pb & synthetic)


# --- CM-04: adherence is current, never a stale or future snapshot -------------------------


def test_adherence_is_current_and_the_same_for_patient_and_care_manager(env):
    client, db = env
    p = patient_with_medication(client, db, "fresh.cm@example.org", last_refill_days=1)
    confirm_all(client, p)
    therapy = p["therapy"]
    today = clock.today()
    # A stale snapshot dated today (written before a fill) and a future one.
    db.query(AdherenceSnapshot).filter(AdherenceSnapshot.therapy_id == therapy).delete()
    for day in (today, today + timedelta(days=1)):
        db.add(
            AdherenceSnapshot(
                patient_id=p["pid"], therapy_id=therapy, as_of_date=day, period_start=day,
                period_end=day, pdc=0.0, mpr=0.0, last_fill_date=None, gap_days=78,
                risk_score=75, risk_segment="high",
            )
        )  # fmt: skip
    db.commit()
    pipeline.refresh_real_patients(db)
    db.commit()
    snaps = db.scalars(
        select(AdherenceSnapshot).where(AdherenceSnapshot.therapy_id == therapy)
    ).all()
    assert all(s.as_of_date <= today for s in snaps)
    patient_view = next(
        m for m in health(client, p["session"])["medications"] if m["therapy_id"] == therapy
    )
    cm_view = next(
        t for t in client.get(f"/api/patients/{p['pid']}", headers=p["cm"]).json()["therapies"]
        if t["therapy_id"] == therapy
    )  # fmt: skip
    for view in (patient_view, cm_view):
        assert view["last_fill_date"] == (today - timedelta(days=1)).isoformat()
        assert view["gap_days"] == 0 and view["pdc"] > 0
    # Days pass without any event: a snapshot from yesterday is never read as today's.
    db.query(AdherenceSnapshot).filter(AdherenceSnapshot.therapy_id == therapy).update(
        {"as_of_date": today - timedelta(days=1)}
    )
    db.commit()
    again = client.get(f"/api/patients/{p['pid']}", headers=p["cm"]).json()["therapies"]
    assert next(t for t in again if t["therapy_id"] == therapy)["gap_days"] == 0
    assert db.scalar(
        select(AdherenceSnapshot.id).where(
            AdherenceSnapshot.therapy_id == therapy, AdherenceSnapshot.as_of_date == today
        )
    )


# --- CM-15: nothing in the future -------------------------------------------------------------


def test_a_refill_logged_today_is_never_stamped_in_the_future(env):
    client, db = env
    p = patient_with_medication(client, db, "time.cm@example.org", last_refill_days=20)
    confirm_all(client, p)
    logged = client.post(f"/api/care/medications/{p['therapy']}/fills", json={}, headers=p["cm"])
    assert logged.status_code == 201, logged.text
    now = utcnow()
    assert db.get(Patient, p["pid"]).last_activity_at <= now
    assert clock.day_instant(clock.today()) <= utcnow()
    assert clock.day_instant(clock.today() - timedelta(days=1)).hour == 12
    fill = db.scalar(
        select(MedicationFill).where(
            MedicationFill.therapy_id == p["therapy"], MedicationFill.fill_date == clock.today()
        )
    )
    assert fill is not None


# --- CM-11 / CM-12 / CM-16: outreach is current, responses reach the CM, errors are structured


def _content(db, channel, measure="hypertension"):
    return next(
        c.content_id
        for c in db.scalars(
            select(Content).where(
                Content.audience == "PATIENT",
                Content.action_type == "refill_nudge",
                Content.mlr_status == "approved",
                Content.measure == measure,
            )
        )  # fmt: skip
        if channel in c.channels
    )


def _nba(db, p, status, channel="portal") -> Nba:
    cycle_id = db.scalar(select(Nba.cycle_id).order_by(Nba.id.desc()))
    today = clock.today()
    nba = Nba(
        cycle_id=cycle_id, target_type=TargetType.PATIENT, target_id=p["pid"],
        therapy_id=p["therapy"], action="refill_nudge", channel=channel,
        content_id=_content(db, channel), status=status, score=1.0, priority=1.0,
        scheduled_for=today, as_of_date=today, rationale="Test.", reason_codes=[],
    )  # fmt: skip
    db.add(nba)
    db.commit()
    return nba


def test_safeguards_are_current_before_the_care_manager_acts(env):
    client, db = env
    p = patient_with_medication(client, db, "gate.cm@example.org")
    confirm_all(client, p)
    me = as_user(p["session"])
    client.put("/api/me/consents/outreach/portal", json={"granted": True}, headers=me)
    nba = _nba(db, p, NbaStatus.READY_FOR_REVIEW)
    detail = client.get(f"/api/nba/{nba.id}", headers=p["cm"]).json()
    assert detail["gate_now"]["ok"] is True
    # The patient withdraws consent after the recommendation was prepared.
    client.put("/api/me/consents/outreach/portal", json={"granted": False}, headers=me)
    detail = client.get(f"/api/nba/{nba.id}", headers=p["cm"]).json()
    assert detail["gate_now"]["ok"] is False and "consent" in detail["gate_now"]["reason"]
    queue = client.get("/api/nba", headers=p["cm"]).json()["items"]
    row = next(i for i in queue if i["id"] == nba.id)
    assert row["gate_now"]["ok"] is False  # queue and detail agree
    approve = client.post(f"/api/nba/{nba.id}/approve", json={}, headers=p["cm"])
    assert approve.status_code == 409 and code_of(approve) == "blocked"
    again = client.post(f"/api/nba/{nba.id}/approve", json={}, headers=p["cm"])
    assert again.status_code == 409 and code_of(again) == "invalid_state"
    assert "blocked" in again.json()["detail"]["message"]


def test_patient_responses_reach_the_care_manager_until_reviewed(env):
    client, db = env
    p = patient_with_medication(client, db, "respond.cm@example.org")
    confirm_all(client, p)
    me, cm = as_user(p["session"]), p["cm"]
    client.put("/api/me/consents/outreach/portal", json={"granted": True}, headers=me)
    nba = _nba(db, p, NbaStatus.APPROVED)
    before = attention(client, cm)["outreach"]
    sent = client.post(f"/api/nba/{nba.id}/send", headers=cm)
    assert sent.status_code == 200, sent.text
    assert attention(client, cm)["outreach"] == before - 1  # no longer waiting to be sent
    interaction = db.scalar(select(Interaction).where(Interaction.nba_id == nba.id))
    answered = client.post(
        f"/api/me/inbox/{interaction.id}/respond", json={"response": "clicked"}, headers=me
    )
    assert answered.status_code == 200
    assert attention(client, cm)["outreach"] == before
    listed = client.get("/api/nba", params={"responses": True}, headers=cm).json()
    assert [i["id"] for i in listed["items"]] == [nba.id]
    assert listed["work"]["responses"] == 1
    reviewed = client.post(f"/api/nba/{nba.id}/response-reviewed", headers=cm)
    assert reviewed.status_code == 200
    assert attention(client, cm)["outreach"] == before - 1
    assert client.get("/api/nba", params={"responses": True}, headers=cm).json()["items"] == []
    # Reviewing twice changes nothing; a recommendation without a response cannot be.
    assert client.post(f"/api/nba/{nba.id}/response-reviewed", headers=cm).status_code == 200


# --- CM-13: one definition for every count ---------------------------------------------------


def test_care_request_counts_agree_with_the_menu(env):
    client, db = env
    p = patient_with_medication(client, db, "counts.cm@example.org")
    confirm_all(client, p)
    cm = p["cm"]
    later = (clock.today() + timedelta(days=14)).isoformat()
    made = client.post(
        f"/api/care/patients/{p['pid']}/notes",
        json={"kind": "follow_up", "text": "Check readings", "due_date": later,
              "visible_to_patient": True},
        headers=cm,
    )  # fmt: skip
    assert made.status_code in (200, 201), made.text
    listed = client.get("/api/care/requests", params={"status": "active"}, headers=cm).json()
    assert listed["work"]["needs_you"] == attention(client, cm)["care_requests"]
    assert listed["work"]["scheduled"] >= 1


# --- CM-16 / CM-17 ------------------------------------------------------------------------------


def test_recommendation_errors_have_a_code_and_a_message(env):
    client, db = env
    cm = auth(client, db.scalar(select(User).where(User.role == "care_manager")).username)
    sent = db.scalar(select(Nba).where(Nba.status == NbaStatus.READY_FOR_REVIEW).order_by(Nba.id))
    mine = client.get("/api/nba", headers=cm).json()["items"]
    target = next((i for i in mine if i["status"] == "ready_for_review"), None) or {"id": sent.id}
    response = client.post(f"/api/nba/{target['id']}/send", headers=cm)
    if response.status_code != 404:
        detail = response.json()["detail"]
        assert response.status_code == 409 and detail["code"] == "invalid_state"
        assert detail["message"] and detail["status"] == "ready_for_review"


def test_invitation_links_use_the_configured_public_address(env):
    client, _ = env
    sent = client.post(
        "/api/invitations",
        json={"email": "link.host@example.org", "role": "care_manager"},
        headers=auth(client, "admin"),
    )
    assert sent.status_code == 201
    base = get_settings().public_base_url.rstrip("/")
    assert sent.json()["dev_link"].startswith(f"{base}/invite/")
    assert "localhost" in get_settings().model_fields["public_base_url"].default


# --- CM-19: an old follow-up note is history, not hidden work ---------------------------------


def test_legacy_follow_up_note_is_not_counted_as_open_work(env):
    client, db = env
    from app.models import CareNote

    p = patient_with_medication(client, db, "legacy.cm@example.org")
    confirm_all(client, p)
    before = attention(client, p["cm"])["care_requests"]
    db.add(
        CareNote(
            patient_id=p["pid"], author_user_id=cm_of(db, p["pid"]).id, kind="follow_up",
            text="Internal: call in two weeks.", visible_to_patient=False, created_at=utcnow(),
        )
    )  # fmt: skip
    db.commit()
    assert attention(client, p["cm"])["care_requests"] == before
    notes = record(client, p["cm"], p["pid"])["notes"]
    assert any(n["kind"] == "follow_up" for n in notes)  # kept for the record


def test_confirming_a_second_report_of_a_confirmed_condition_is_refused(env):
    client, db = env
    from app.models import PatientCondition

    p = patient_with_medication(client, db, "second.cm@example.org")
    confirm_all(client, p)
    # A duplicate report left from before the patient-side guard.
    dup = PatientCondition(
        patient_id=p["pid"], condition="hypertension", origin="patient_reported",
        status="reported", reported_at=utcnow(),
    )  # fmt: skip
    db.add(dup)
    db.commit()
    refused = client.post(
        f"/api/care/conditions/{dup.id}/status", json={"status": "confirmed"}, headers=p["cm"]
    )
    assert refused.status_code == 409 and code_of(refused) == "duplicate_condition"
    dismissed = client.post(
        f"/api/care/conditions/{dup.id}/status",
        json={"status": "dismissed", "reason": "Already on your record."},
        headers=p["cm"],
    )
    assert dismissed.status_code == 200


def test_a_dismissed_condition_is_not_a_routing_reason(env):
    client, db = env
    p = patient_with_medication(client, db, "noroute.cm@example.org")
    cm = p["cm"]
    client.post(
        f"/api/care/conditions/{p['condition']}/status",
        json={"status": "dismissed", "reason": "Recorded in error."},
        headers=cm,
    )
    hcp_id = db.scalar(select(User.hcp_id).where(User.username == "hcp0001"))
    refused = client.put(
        f"/api/care/patients/{p['pid']}/hcp",
        json={"hcp_id": hcp_id, "condition_id": p["condition"]},
        headers=cm,
    )
    assert refused.status_code == 409 and code_of(refused) == "condition_not_added"
