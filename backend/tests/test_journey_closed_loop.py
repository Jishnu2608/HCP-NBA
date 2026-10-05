"""The patient journey as one closed loop (the breakpoints of the live audit, B1-B21).

A patient reports, the care manager confirms and routes, the HCP answers in the app, the
care manager closes and follows up, the patient sees each step; adherence is current after
every action; nothing is counted twice; and a real patient always has a care manager.
"""

from datetime import date, timedelta

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
from app.core import clock
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.engagement import delivery
from app.main import app
from app.models import (
    AdherenceSnapshot,
    CareManagerPatient,
    CareRequest,
    Consent,
    Interaction,
    MedicationFill,
    Nba,
    PatientTherapy,
    User,
)
from app.models.enums import NbaStatus, TargetType
from app.models.tables import utcnow


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=73, n_patients=160, n_hcps=40, n_reps=3, n_care_managers=3))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def join(client, email, name="Reon Test", country="GB", region=None) -> dict:
    body = {
        "name": name, "email": email, "date_of_birth": ADULT_DOB, "password": PASSWORD,
        "confirm_password": PASSWORD, **PATIENT_AGREEMENTS, "country": country, "region": region,
    }  # fmt: skip
    return signed_in(verify(client, challenge_of(client.post("/api/auth/signup", json=body))))


def cm_of(db, pid) -> User:
    uid = db.scalar(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    )
    return db.get(User, uid) if uid else None


def health(client, session) -> dict:
    return client.get("/api/me/health", headers=as_user(session)).json()


def requests_of(client, cm, pid) -> list[dict]:
    items = client.get("/api/care/requests", params={"status": "active"}, headers=cm).json()
    return [r for r in items["items"] if r["patient_id"] == pid]


MED = {"name": "lisinopril", "dose_instructions": "10 mg daily", "schedule": "once daily",
       "end_date": None, "ongoing": True}  # fmt: skip


@pytest.fixture(scope="module")
def reon(env):
    """A patient with a confirmed condition and a confirmed medication."""
    client, db = env
    s = join(client, "reon.loop@example.org")
    pid = s["user"]["patient_id"]
    me = as_user(s)
    client.post("/api/me/conditions", json={"condition": "hypertension"}, headers=me)
    start = (clock.today() - timedelta(days=78)).isoformat()
    last = (clock.today() - timedelta(days=10)).isoformat()
    body = {**MED, "start_date": start, "last_refill_date": last}
    assert client.post("/api/me/medications", json=body, headers=me).status_code == 201
    cm = auth(client, cm_of(db, pid).username)
    record = client.get(f"/api/care/patients/{pid}", headers=cm).json()
    cond = record["conditions"][0]["id"]
    therapy = record["medications"][0]["therapy_id"]
    client.post(f"/api/care/conditions/{cond}/status", json={"status": "confirmed"}, headers=cm)
    confirmed = client.post(
        f"/api/care/medications/{therapy}/confirm", json={"days_supply": 30}, headers=cm
    )
    assert confirmed.status_code == 200, confirmed.text
    return {"session": s, "pid": pid, "cm": cm, "condition": cond, "therapy": therapy}


# --- B2: request status changes return the care record -----------------------------------


def test_request_status_change_returns_the_record(env, reon):
    client, db = env
    s, cm = reon["session"], reon["cm"]
    client.post("/api/me/care-requests", json={"reason": "Morning headaches"}, headers=as_user(s))
    request = next(r for r in requests_of(client, cm, reon["pid"]) if r["type"] == "consultation")
    response = client.patch(
        f"/api/care/requests/{request['id']}", json={"status": "in_progress"}, headers=cm
    )
    assert response.status_code == 200
    body = response.json()
    assert body["request"]["status"] == "in_progress"
    assert {"conditions", "medications", "requests", "care_team"} <= set(body["record"])


# --- B1 / B12: the consultation stays alive until the HCP answered and the CM closed it ---


def test_consultation_closed_loop(env, reon):
    client, db = env
    s, cm, pid = reon["session"], reon["cm"], reon["pid"]
    request = next(r for r in requests_of(client, cm, pid) if r["type"] == "consultation")
    hcp_user = db.scalar(select(User).where(User.username == "hcp0001"))
    options = client.get(
        f"/api/care/patients/{pid}/hcp-options",
        params={"condition_id": reon["condition"]},
        headers=cm,
    ).json()
    picked = next(o for o in options["items"] if o["hcp_id"] == hcp_user.hcp_id)
    assert picked["in_app"] is True
    routed = client.put(
        f"/api/care/patients/{pid}/hcp",
        json={"hcp_id": hcp_user.hcp_id, "request_id": request["id"]},
        headers=cm,
    )
    assert routed.status_code == 200
    # Routed, not closed: the patient sees who it is waiting for.
    mine = next(r for r in health(client, s)["requests"] if r["id"] == request["id"])
    assert mine["status"] == "awaiting_hcp" and mine["status_label"].startswith("Waiting for")

    # The HCP sees it, with what the patient asked and their current medication.
    hcp = auth(client, "hcp0001")
    listing = client.get("/api/me/consultations", headers=hcp).json()
    item = next(i for i in listing["items"] if i["id"] == request["id"])
    assert listing["waiting"] >= 1 and item["reason"] == "Morning headaches"
    assert item["medications"] and item["medications"][0]["drug_name"] == "lisinopril"
    assert client.get("/api/me/attention", headers=hcp).json()["consultations"] >= 1
    # Another HCP cannot see or answer it.
    other = auth(client, "hcp0002")
    assert all(
        i["id"] != request["id"]
        for i in client.get("/api/me/consultations", headers=other).json()["items"]
    )  # noqa: E501
    assert (
        client.post(
            f"/api/me/consultations/{request['id']}/respond",
            json={"response": "advice", "message": "x"},
            headers=other,
        ).status_code
        == 404
    )
    # A patient cannot use the HCP endpoint at all.
    assert client.get("/api/me/consultations", headers=as_user(s)).status_code == 403

    answered = client.post(
        f"/api/me/consultations/{request['id']}/respond",
        json={
            "response": "advice",
            "message": "Take it in the evening and check your blood pressure twice a week.",
            "note_to_care_team": "Review in four weeks.",
        },
        headers=hcp,
    )
    assert answered.status_code == 200 and answered.json()["status"] == "hcp_responded"
    # Back with the CM, who sees the internal note; the patient sees the advice only.
    cm_view = next(r for r in requests_of(client, cm, pid) if r["id"] == request["id"])
    assert cm_view["status"] == "hcp_responded" and len(cm_view["notes"]) == 2
    mine = next(r for r in health(client, s)["requests"] if r["id"] == request["id"])
    assert mine["status_label"].endswith("responded") and len(mine["notes"]) == 1
    instruction = next(n for n in health(client, s)["notes"] if n["request"])
    assert instruction["request"]["id"] == request["id"] and instruction["by_hcp"] is True

    closed = client.patch(
        f"/api/care/requests/{request['id']}",
        json={"status": "closed", "resolution": "Advice shared; follow-up booked."},
        headers=cm,
    )
    assert closed.status_code == 200
    mine = next(r for r in health(client, s)["requests"] if r["id"] == request["id"])
    assert mine["status"] == "closed"


def test_declined_consultation_returns_to_the_care_manager(env, reon):
    client, db = env
    s, cm, pid = reon["session"], reon["cm"], reon["pid"]
    client.post("/api/me/care-requests", json={"reason": "Dizziness"}, headers=as_user(s))
    request = next(
        r for r in requests_of(client, cm, pid)
        if r["type"] == "consultation" and r["status"] == "open"
    )  # fmt: skip
    hcp_user = db.scalar(select(User).where(User.username == "hcp0001"))
    client.put(
        f"/api/care/patients/{pid}/hcp",
        json={"hcp_id": hcp_user.hcp_id, "request_id": request["id"]},
        headers=cm,
    )
    declined = client.post(
        f"/api/me/consultations/{request['id']}/respond",
        json={"response": "decline", "note_to_care_team": "Not my specialty."},
        headers=auth(client, "hcp0001"),
    )
    assert declined.status_code == 200
    back = next(r for r in requests_of(client, cm, pid) if r["id"] == request["id"])
    assert back["status"] == "open" and back["assigned_hcp"] is None
    assert "Not my specialty" in back["resolution"]
    # Closed so later tests start from a clean slate.
    client.patch(f"/api/care/requests/{request['id']}", json={"status": "closed"}, headers=cm)


def test_cm_records_an_off_platform_hcp_response(env, reon):
    client, db = env
    s, cm, pid = reon["session"], reon["cm"], reon["pid"]
    client.post("/api/me/care-requests", json={"reason": "Second opinion"}, headers=as_user(s))
    request = next(
        r for r in requests_of(client, cm, pid)
        if r["type"] == "consultation" and r["status"] == "open"
    )  # fmt: skip
    options = client.get(
        f"/api/care/patients/{pid}/hcp-options",
        params={"condition_id": reon["condition"]},
        headers=cm,
    ).json()
    offline = next(o for o in options["items"] if not o["in_app"])
    client.put(
        f"/api/care/patients/{pid}/hcp",
        json={"hcp_id": offline["hcp_id"], "request_id": request["id"]},
        headers=cm,
    )
    recorded = client.post(
        f"/api/care/requests/{request['id']}/hcp-response",
        json={"response": "advice", "message": "Keep the current dose."},
        headers=cm,
    )
    assert recorded.status_code == 200
    back = next(r for r in requests_of(client, cm, pid) if r["id"] == request["id"])
    assert back["status"] == "hcp_responded"
    client.patch(f"/api/care/requests/{request['id']}", json={"status": "closed"}, headers=cm)


# --- B3: one time base ----------------------------------------------------------------------


def test_engine_send_is_stamped_now_in_utc(env, reon):
    client, db = env
    stamp = delivery.now_on(db)
    assert abs((stamp - utcnow()).total_seconds()) < 5
    assert clock.today() == utcnow().date()


# --- B4 / B5: adherence is current right away; a medication already taken is not "never filled"


def test_adherence_is_current_after_confirmation_and_refill(env, reon):
    client, db = env
    s = reon["session"]
    med = health(client, s)["medications"][0]
    # The last refill the patient gave became a fill at confirmation: covered, not a 78-day gap.
    assert med["last_fill_date"] == (clock.today() - timedelta(days=10)).isoformat()
    assert med["gap_days"] == 0 and med["pdc"] is not None
    fills_before = db.scalars(
        select(MedicationFill).where(MedicationFill.therapy_id == reon["therapy"])
    ).all()
    assert len(fills_before) == 1 and fills_before[0].source == "care_manager"
    refilled = client.post(
        f"/api/me/medications/{reon['therapy']}/refill", json={}, headers=as_user(s)
    )
    assert refilled.status_code == 201
    med = next(m for m in refilled.json()["medications"] if m["therapy_id"] == reon["therapy"])
    assert med["last_fill_date"] == clock.today().isoformat()
    snapshot = db.scalar(
        select(AdherenceSnapshot).where(
            AdherenceSnapshot.therapy_id == reon["therapy"],
            AdherenceSnapshot.as_of_date == clock.today(),
        )
    )
    assert snapshot is not None and snapshot.last_fill_date == clock.today()


# --- B9: one refill per medication per day, through one path --------------------------------


def test_same_day_refill_is_not_counted_twice(env, reon):
    client, db = env
    s, therapy = reon["session"], reon["therapy"]
    again = client.post(f"/api/me/medications/{therapy}/refill", json={}, headers=as_user(s))
    assert again.status_code == 409 and code_of(again) == "refill_already_recorded"
    cm_again = client.post(f"/api/care/medications/{therapy}/fills", json={}, headers=reon["cm"])
    assert cm_again.status_code == 409
    today_fills = db.scalars(
        select(MedicationFill).where(
            MedicationFill.therapy_id == therapy, MedicationFill.fill_date == clock.today()
        )
    ).all()
    assert len(today_fills) == 1


def _send_to(client, db, reon, channel="portal") -> Interaction:
    """An approved refill message for the patient's medication, sent by their CM."""
    client.put(
        f"/api/me/consents/outreach/{channel}", json={"granted": True},
        headers=as_user(reon["session"]),
    )  # fmt: skip
    nba = db.scalar(
        select(Nba).where(Nba.target_id == reon["pid"]).order_by(Nba.id.desc())
    ) or db.scalar(select(Nba).where(Nba.target_type == TargetType.PATIENT))
    clone = make_nba(
        cycle_id=nba.cycle_id,
        target_id=reon["pid"],
        action="refill_nudge",
        channel=channel,
        content_id=_content(db, channel),
        therapy_id=reon["therapy"],
        status=NbaStatus.APPROVED,
    )
    db.add(clone)
    db.commit()
    sent = client.post(f"/api/nba/{clone.id}/send", headers=reon["cm"])
    assert sent.status_code == 200, sent.text
    return db.scalar(select(Interaction).where(Interaction.nba_id == clone.id))


def make_nba(**fields) -> Nba:
    """A stored recommendation, as the engine writes one."""
    today = clock.today()
    return Nba(
        target_type=TargetType.PATIENT, score=1.0, priority=1.0, scheduled_for=today,
        as_of_date=today, rationale="Test recommendation.", reason_codes=[], **fields,
    )  # fmt: skip


def _content(db, channel):
    from app.models import Content

    return next(
        c.content_id
        for c in db.scalars(
            select(Content).where(
                Content.audience == "PATIENT",
                Content.action_type == "refill_nudge",
                Content.mlr_status == "approved",
                Content.measure == "hypertension",
            )
        )
        if channel in c.channels
    )


def test_inbox_refill_uses_the_same_checks(env, reon):
    client, db = env
    interaction = _send_to(client, db, reon)
    assert abs((interaction.int_ts - utcnow()).total_seconds()) < 5
    s = as_user(reon["session"])
    inbox = client.get("/api/me/inbox", headers=s).json()
    item = next(i for i in inbox if i["id"] == interaction.id)
    assert item["channel_note"] == "Portal message"
    assert client.get("/api/me/attention", headers=s).json()["messages"] >= 1
    # Already refilled today: the message is answered without a second fill.
    answered = client.post(
        f"/api/me/inbox/{interaction.id}/respond", json={"response": "refill"}, headers=s
    )
    assert answered.status_code == 200 and answered.json()["status"] == "filled"
    today_fills = db.scalars(
        select(MedicationFill).where(
            MedicationFill.therapy_id == reon["therapy"], MedicationFill.fill_date == clock.today()
        )
    ).all()
    assert len(today_fills) == 1


# --- B8: duplicates ------------------------------------------------------------------------


def test_duplicates_are_explained_and_can_be_dismissed(env, reon):
    client, db = env
    s, cm, pid = as_user(reon["session"]), reon["cm"], reon["pid"]
    again = client.post("/api/me/conditions", json={"condition": "hypertension"}, headers=s)
    assert again.status_code == 409 and code_of(again) == "duplicate_condition"
    med = {**MED, "start_date": (clock.today() - timedelta(days=5)).isoformat()}
    again = client.post("/api/me/medications", json=med, headers=s)
    assert again.status_code == 409 and code_of(again) == "duplicate_medication"
    client.post("/api/me/care-requests", json={"reason": "One"}, headers=s)
    again = client.post("/api/me/care-requests", json={"reason": "Two"}, headers=s)
    assert again.status_code == 409 and code_of(again) == "consultation_open"
    # A reported entry the CM decides does not belong is dismissed, and the patient is told.
    client.post(
        "/api/me/conditions", json={"condition": "other", "other_text": "Headache"}, headers=s
    )
    reported = next(
        c for c in health(client, reon["session"])["conditions"] if c["status"] == "reported"
    )
    dismissed = client.post(
        f"/api/care/conditions/{reported['id']}/status",
        json={"status": "dismissed", "reason": "Recorded as part of your consultation"},
        headers=cm,
    )
    assert dismissed.status_code == 200
    view = health(client, reon["session"])
    assert next(c for c in view["conditions"] if c["id"] == reported["id"])["status"] == "dismissed"
    request = next(
        r for r in view["requests"] if r["condition"] and r["condition"]["id"] == reported["id"]
    )
    assert request["status"] == "closed" and request["resolution"].startswith("Not added")
    # The open consultation from above, closed for the next tests.
    for r in requests_of(client, cm, pid):
        client.patch(f"/api/care/requests/{r['id']}", json={"status": "closed"}, headers=cm)


# --- B13: a stopped medication stops its outreach ---------------------------------------------


def test_stopping_a_medication_stops_its_recommendations(env):
    client, db = env
    s = join(client, "stop.case@example.org", "Stop Case")
    pid = s["user"]["patient_id"]
    start = (clock.today() - timedelta(days=60)).isoformat()
    client.post("/api/me/medications", json={**MED, "start_date": start}, headers=as_user(s))
    cm = auth(client, cm_of(db, pid).username)
    therapy = client.get(f"/api/care/patients/{pid}", headers=cm).json()["medications"][0]
    client.post(
        f"/api/care/medications/{therapy['therapy_id']}/confirm", json={"days_supply": 30},
        headers=cm,
    )  # fmt: skip
    pending = make_nba(
        cycle_id=db.scalar(select(Nba.cycle_id)),
        target_id=pid,
        action="refill_nudge",
        channel="portal",
        content_id=_content(db, "portal"),
        therapy_id=therapy["therapy_id"],
        status=NbaStatus.READY_FOR_REVIEW,
    )
    approved = make_nba(
        cycle_id=pending.cycle_id,
        target_id=pid,
        action="education",
        channel="portal",
        content_id=_content(db, "portal"),
        therapy_id=therapy["therapy_id"],
        status=NbaStatus.APPROVED,
    )
    db.add_all([pending, approved])
    db.commit()
    client.post(f"/api/care/medications/{therapy['therapy_id']}/stop", headers=cm)
    db.refresh(pending)
    db.refresh(approved)
    assert pending.status == approved.status == NbaStatus.EXPIRED
    # Even if one survived, approval and send refuse a stopped medication.
    approved.status = NbaStatus.APPROVED
    db.commit()
    refused = client.post(f"/api/nba/{approved.id}/send", headers=cm)
    assert refused.status_code == 409 and "no longer active" in refused.text
    blocked = client.post(
        f"/api/me/medications/{therapy['therapy_id']}/refill", json={}, headers=as_user(s)
    )
    assert blocked.status_code == 409 and code_of(blocked) == "not_active"


# --- B11 / B12: a follow-up is owned work with a due date ------------------------------------


def test_follow_up_is_owned_work_with_a_due_date(env, reon):
    client, db = env
    cm, pid = reon["cm"], reon["pid"]
    due = (clock.today() + timedelta(days=14)).isoformat()
    created = client.post(
        f"/api/care/patients/{pid}/notes",
        json={"kind": "follow_up", "text": "Call about home readings", "due_date": due},
        headers=cm,
    )
    assert created.status_code == 201
    follow_up = next(r for r in requests_of(client, cm, pid) if r["type"] == "follow_up")
    assert follow_up["due_date"] == due and follow_up["owner"] == cm_of(db, pid).display_name
    mine = next(r for r in health(client, reon["session"])["requests"] if r["type"] == "follow_up")
    assert mine["status_label"].startswith("Your care team will follow up by")
    missing = client.post(
        f"/api/care/patients/{pid}/notes", json={"kind": "follow_up", "text": "No date"},
        headers=cm,
    )  # fmt: skip
    assert missing.status_code == 422
    # Overdue follow-ups count as work needing the care manager.
    row = db.get(CareRequest, follow_up["id"])
    row.due_date = clock.today() - timedelta(days=1)
    db.commit()
    assert client.get("/api/me/attention", headers=cm).json()["care_requests"] >= 1
    overdue = next(r for r in requests_of(client, cm, pid) if r["id"] == follow_up["id"])
    assert overdue["overdue"] is True
    done = client.patch(
        f"/api/care/requests/{follow_up['id']}",
        json={"status": "closed", "resolution": "Called; readings fine."},
        headers=cm,
    )
    assert done.status_code == 200


# --- B10: geography is explicit -----------------------------------------------------------


def test_routing_shows_country_and_says_when_none_is_local(env, reon):
    client, db = env
    options = client.get(
        f"/api/care/patients/{reon['pid']}/hcp-options",
        params={"condition_id": reon["condition"]},
        headers=reon["cm"],
    ).json()
    assert options["patient_country"] == "GB" and options["local_match"] is False
    assert all(o["country_label"] in ("United States", None) for o in options["items"])


# --- B15: a disabled HCP is not routable, and their waiting consultations come back ----------


def test_disabled_hcp_is_not_routable_and_work_returns(env):
    client, db = env
    s = join(client, "hcp.off@example.org", "Hana Off")
    pid = s["user"]["patient_id"]
    client.post("/api/me/conditions", json={"condition": "hypertension"}, headers=as_user(s))
    client.post("/api/me/care-requests", json={"reason": "Check-up"}, headers=as_user(s))
    cm = auth(client, cm_of(db, pid).username)
    request = next(r for r in requests_of(client, cm, pid) if r["type"] == "consultation")
    hcp_user = db.scalar(select(User).where(User.username == "hcp0003"))
    client.put(
        f"/api/care/patients/{pid}/hcp",
        json={"hcp_id": hcp_user.hcp_id, "request_id": request["id"]},
        headers=cm,
    )
    admin = auth(client, "admin")
    impact = client.get(f"/api/admin/users/{hcp_user.id}/impact", headers=admin).json()
    assert impact["consultations"] == 1
    client.patch(
        f"/api/admin/users/{hcp_user.id}/status", json={"status": "disabled"}, headers=admin
    )
    back = next(r for r in requests_of(client, cm, pid) if r["id"] == request["id"])
    assert back["status"] == "open" and "no longer available" in back["resolution"]
    options = client.get(f"/api/care/patients/{pid}/hcp-options", headers=cm).json()
    assert all(o["hcp_id"] != hcp_user.hcp_id for o in options["items"])
    team = health(client, s)["care_team"]["hcps"]
    assert any(h["hcp_id"] == hcp_user.hcp_id and h["available"] is False for h in team)
    refused = client.put(
        f"/api/care/patients/{pid}/hcp", json={"hcp_id": hcp_user.hcp_id}, headers=cm
    )
    assert refused.status_code == 409 and code_of(refused) == "hcp_unavailable"
    client.patch(f"/api/admin/users/{hcp_user.id}/status", json={"status": "active"}, headers=admin)


# --- B16 / B7: what staff see is what is true ------------------------------------------------


def test_patient_360_counts_channels_and_shows_the_care_manager(env, reon):
    client, db = env
    view = client.get(f"/api/patients/{reon['pid']}", headers=reon["cm"]).json()
    assert view["outreach_channels_total"] == 4
    assert view["outreach_channels_granted"] == 1  # portal, granted in the inbox test
    assert view["care_managers"] and view["last_cycle_at"]
    queue = client.get(
        "/api/nba", params={"status": ["ready_for_review", "sent", "responded"], "limit": 200},
        headers=reon["cm"],
    ).json()  # fmt: skip
    assert {n["target_origin"] for n in queue["items"]} <= {
        "synthetic",
        "self_registered",
        "clinic",
    }


# --- B18: consent history is never deleted ---------------------------------------------------


def test_same_day_consent_changes_keep_their_history(env, reon):
    client, db = env
    s = as_user(reon["session"])
    client.put("/api/me/consents/outreach/email", json={"granted": True}, headers=s)
    client.put("/api/me/consents/outreach/email", json={"granted": False}, headers=s)
    rows = db.scalars(
        select(Consent).where(Consent.patient_id == reon["pid"], Consent.channel == "email")
    ).all()
    assert [r.granted for r in rows] == [True, False]
    assert rows[0].effective_to == clock.today() and rows[1].effective_to is None
    current = {
        (c["purpose"], c["channel"]): c["granted"]
        for c in client.get("/api/me/consents", headers=s).json()
    }
    assert current[("outreach", "email")] is False


# --- B14: a real patient always has one active care manager ----------------------------------


def test_disabling_a_care_manager_moves_their_real_patients(env, reon):
    client, db = env
    admin = auth(client, "admin")
    pid = reon["pid"]
    old = cm_of(db, pid)
    impact = client.get(f"/api/admin/users/{old.id}/impact", headers=admin).json()
    assert impact["real_patients"] >= 1 and impact["to"] is not None
    client.patch(f"/api/admin/users/{old.id}/status", json={"status": "disabled"}, headers=admin)
    new = cm_of(db, pid)
    assert new.id == impact["to"]["id"] != old.id
    owners = db.scalars(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    ).all()
    assert owners == [new.id]
    client.patch(f"/api/admin/users/{old.id}/status", json={"status": "active"}, headers=admin)


def test_panel_edit_cannot_orphan_and_assignment_moves_the_patient(env, reon):
    client, db = env
    admin = auth(client, "admin")
    pid = reon["pid"]
    current = cm_of(db, pid)
    panel = client.get(f"/api/admin/users/{current.id}", headers=admin).json()["patients"]
    without = [p["patient_id"] for p in panel if p["patient_id"] != pid]
    refused = client.put(
        f"/api/admin/users/{current.id}/assignments", json={"patient_ids": without}, headers=admin
    )
    assert refused.status_code == 409 and code_of(refused) == "would_orphan"
    other = next(
        u for u in db.scalars(select(User).where(User.role == "care_manager"))
        if u.id != current.id and u.status == "active"
    )  # fmt: skip
    other_panel = client.get(f"/api/admin/users/{other.id}", headers=admin).json()["patients"]
    moved = client.put(
        f"/api/admin/users/{other.id}/assignments",
        json={"patient_ids": [p["patient_id"] for p in other_panel] + [pid]},
        headers=admin,
    )
    assert moved.status_code == 200
    owners = db.scalars(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    ).all()
    assert owners == [other.id]


def test_sign_up_without_any_care_manager_is_held_then_adopted(env):
    client, db = env
    admin = auth(client, "admin")
    managers = db.scalars(select(User).where(User.role == "care_manager")).all()
    states = {u.id: u.status for u in managers}
    for u in managers:
        u.status = "disabled"
    db.commit()
    s = join(client, "nobody.home@example.org", "Nina Waiting")
    pid = s["user"]["patient_id"]
    assert health(client, s)["care_team"]["care_managers"] == []
    assert (
        client.get("/api/me/attention", headers=admin).json()["patients_without_care_manager"] >= 1
    )
    waiting = client.get("/api/admin/users/attention/unassigned", headers=admin).json()
    assert any(p["patient_id"] == pid for p in waiting)
    first = managers[0]
    first.status = "disabled"
    db.commit()
    activated = client.patch(
        f"/api/admin/users/{first.id}/status", json={"status": "active"}, headers=admin
    )
    assert activated.status_code == 200, activated.text
    assert cm_of(db, pid) is not None and cm_of(db, pid).id == first.id
    for u in managers:
        u.status = states[u.id]
    db.commit()


# --- B17: the education message contains the guide --------------------------------------------


def test_education_content_is_the_guide_itself(env):
    _, db = env
    from app.models import Content

    guide = db.scalar(
        select(Content).where(
            Content.audience == "PATIENT",
            Content.action_type == "education",
            Content.topic == "hypertension_adherence",
        )
    )
    assert "Take it at the same time each day" in guide.body
    assert not guide.body.startswith("A short, plain-language guide")


def test_no_reported_medication_or_stopped_therapy_reaches_the_engine(env, reon):
    _, db = env
    from app.features.population import load_population

    pop = load_population(db, patient_id=reon["pid"])
    assert [t.id for t in pop.therapies.get(reon["pid"], [])] == [reon["therapy"]]
    assert all(t.review_status == "confirmed" for t in pop.therapies[reon["pid"]])
    assert date.today() - clock.today() in (timedelta(0), timedelta(days=1), timedelta(days=-1))
    assert db.scalar(select(PatientTherapy).where(PatientTherapy.id == reon["therapy"]))
