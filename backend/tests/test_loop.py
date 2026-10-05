"""The closed loop: approve, send, respond, play out, real days pass, next recommendation."""

from datetime import timedelta

import pytest
from conftest import ApiClient, auth, cookie_header, new_session, session_of
from sqlalchemy import select

from app import cycle
from app.core import clock
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.features import engagement as eng
from app.features.population import load_population
from app.main import app
from app.models import AuditLog, Interaction, MedicationFill, Nba, Patient
from app.models.enums import NbaStatus, Outcome

INBOX = ("portal", "email", "sms")
# The synthetic history ends on the real date it was generated; a week later is simulated in
# tests by pinning the date (the application itself always uses the real date).
DEFAULT_AS_OF = clock.today()
WEEK_LATER = DEFAULT_AS_OF + timedelta(days=7)


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=31, n_patients=220, n_hcps=40, n_reps=4, n_care_managers=3))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    client = ApiClient(app)
    yield client, db
    app.dependency_overrides.clear()
    db.close()


def call(env, username, method, path, **kw):
    client, _ = env
    return client.request(method, path, headers=auth(client, username), **kw)


def ready(db, target_id) -> Nba:
    return db.scalar(
        select(Nba).where(Nba.target_id == target_id, Nba.status == NbaStatus.READY_FOR_REVIEW)
    )


def approve_and_send(env, username, nba) -> dict:
    assert call(env, username, "POST", f"/api/nba/{nba.id}/approve", json={}).status_code == 200
    sent = call(env, username, "POST", f"/api/nba/{nba.id}/send")
    assert sent.status_code == 200, sent.text
    return sent.json()


def test_send_requires_approval_and_records_a_pending_interaction(env):
    _, db = env
    nba = ready(db, "PAT_00001")
    assert nba.channel == "sms"
    assert call(env, "cm01", "POST", f"/api/nba/{nba.id}/send").status_code == 409
    detail = approve_and_send(env, "cm01", nba)
    assert detail["status"] == "sent"
    assert [a["action"] for a in detail["audit"]][-2:] == ["nba_approved", "nba_sent"]
    assert detail["audit"][-1]["detail"]["adapter"] == "simulated_sms"

    i = db.scalar(select(Interaction).where(Interaction.nba_id == nba.id))
    assert (i.outcome, i.source, i.channel, i.therapy_id) == (
        "pending",
        "nba",
        "sms",
        nba.therapy_id,
    )
    assert i.int_ts.date() == DEFAULT_AS_OF
    assert call(env, "cm01", "POST", f"/api/nba/{nba.id}/send").status_code == 409


def test_pending_touch_counts_for_frequency_but_not_for_response_rates(env):
    _, db = env
    state = eng.patient_state(load_population(db), "PAT_00001")
    # Hero history has 2 text messages (both answered); the pending one is not a failure yet.
    assert (state.sent[("channel", "sms")], state.engaged[("channel", "sms")]) == (2, 2)
    assert state.recent(DEFAULT_AS_OF, 0) == 1


def test_patient_reads_message_then_refills_and_adherence_updates(env):
    _, db = env
    inbox = call(env, "pat00001", "GET", "/api/me/inbox").json()
    assert len(inbox) == 1 and inbox[0]["status"] == "pending" and "Margaret" in inbox[0]["body"]
    msg = inbox[0]["id"]
    # Someone else's message id is indistinguishable from a missing one.
    assert (
        call(
            env, "pat00002", "POST", f"/api/me/inbox/{msg}/respond", json={"response": "opened"}
        ).status_code
        == 404
    )
    assert (
        call(
            env, "hcp0001", "POST", f"/api/me/inbox/{msg}/respond", json={"response": "opened"}
        ).status_code
        == 404
    )

    opened = call(
        env, "pat00001", "POST", f"/api/me/inbox/{msg}/respond", json={"response": "opened"}
    )
    assert opened.json()["status"] == "opened"
    fills_before = len(
        db.scalars(select(MedicationFill).where(MedicationFill.patient_id == "PAT_00001")).all()
    )
    refilled = call(
        env, "pat00001", "POST", f"/api/me/inbox/{msg}/respond", json={"response": "refill"}
    )
    assert refilled.json()["status"] == "filled" and refilled.json()["can_refill"] is False
    fills = db.scalars(
        select(MedicationFill)
        .where(MedicationFill.patient_id == "PAT_00001")
        .order_by(MedicationFill.fill_date)
    ).all()
    assert len(fills) == fills_before + 1 and fills[-1].fill_date == DEFAULT_AS_OF
    assert (
        call(
            env, "pat00001", "POST", f"/api/me/inbox/{msg}/respond", json={"response": "refill"}
        ).status_code
        == 409
    )
    # A later "opened" must not downgrade the captured fill.
    again = call(
        env, "pat00001", "POST", f"/api/me/inbox/{msg}/respond", json={"response": "opened"}
    )
    assert again.json()["status"] == "filled"

    nba = db.scalar(
        select(Nba).where(Nba.target_id == "PAT_00001", Nba.status == NbaStatus.RESPONDED)
    )
    log = db.scalar(
        select(AuditLog)
        .where(AuditLog.nba_id == nba.id, AuditLog.action == "response_captured")
        .order_by(AuditLog.id.desc())
    )
    assert (log.actor, log.actor_role, log.detail["outcome"]) == ("pat00001", "patient", "filled")

    # Next cycle: the gap is closed, risk falls, and no new reminder is raised.
    assert call(env, "admin", "POST", "/api/admin/cycle").status_code == 200
    profile = call(env, "cm01", "GET", "/api/patients/PAT_00001").json()
    assert profile["therapies"][0]["gap_days"] == 0
    assert profile["risk_segment"] != "high"
    assert profile["open_nba"] is None


def test_hcp_receives_content_in_portal_and_click_is_captured(env):
    _, db = env
    nba = ready(db, "HCP_0001")
    assert nba.channel in INBOX
    approve_and_send(env, "rep01", nba)
    inbox = call(env, "hcp0001", "GET", "/api/me/inbox").json()
    assert len(inbox) == 1 and inbox[0]["subject"] and inbox[0]["can_refill"] is False
    clicked = call(
        env,
        "hcp0001",
        "POST",
        f"/api/me/inbox/{inbox[0]['id']}/respond",
        json={"response": "clicked"},
    )
    assert clicked.json()["status"] == "clicked"
    refill = call(
        env,
        "hcp0001",
        "POST",
        f"/api/me/inbox/{inbox[0]['id']}/respond",
        json={"response": "refill"},
    )
    assert refill.status_code == 409
    db.refresh(nba)
    assert nba.status == NbaStatus.RESPONDED


def test_send_is_blocked_if_consent_is_withdrawn_after_approval(env):
    _, db = env
    nba = ready(db, "PAT_00005")
    assert call(env, "cm01", "POST", f"/api/nba/{nba.id}/approve", json={}).status_code == 200
    off = call(
        env, "pat00005", "PUT", f"/api/me/consents/outreach/{nba.channel}", json={"granted": False}
    )
    assert off.status_code == 200
    blocked = call(env, "cm01", "POST", f"/api/nba/{nba.id}/send")
    assert blocked.status_code == 409 and "not consented" in blocked.json()["detail"]
    db.refresh(nba)
    assert nba.status == NbaStatus.BLOCKED
    assert db.scalar(select(Interaction).where(Interaction.nba_id == nba.id)) is None
    log = db.scalar(
        select(AuditLog).where(AuditLog.nba_id == nba.id, AuditLog.action == "nba_blocked_at_send")
    )
    assert log.consent_ok is False


def test_staff_log_the_outcome_of_calls_but_not_of_digital_messages(env):
    _, db = env
    phone = db.scalar(
        select(Nba)
        .where(Nba.status == NbaStatus.READY_FOR_REVIEW, Nba.channel == "phone")
        .order_by(Nba.priority.desc())
    )
    approve_and_send(env, "admin", phone)
    assert (
        call(
            env, "admin", "POST", f"/api/nba/{phone.id}/outcome", json={"outcome": "maybe"}
        ).status_code
        == 422
    )
    done = call(
        env, "admin", "POST", f"/api/nba/{phone.id}/outcome",
        json={"outcome": "completed", "note": "Agreed to refill this week"},
    )  # fmt: skip
    assert done.json()["status"] == "responded"
    assert done.json()["audit"][-1]["reason"] == "Agreed to refill this week"
    assert (
        call(
            env, "admin", "POST", f"/api/nba/{phone.id}/outcome", json={"outcome": "completed"}
        ).status_code
        == 409
    )

    digital = db.scalar(
        select(Nba).where(
            Nba.status == NbaStatus.READY_FOR_REVIEW,
            Nba.channel == "email",
            Nba.target_type == "PATIENT",
        )
    )
    approve_and_send(env, "admin", digital)
    assert (
        call(
            env, "admin", "POST", f"/api/nba/{digital.id}/outcome", json={"outcome": "completed"}
        ).status_code
        == 409
    )


def test_bulk_send_goes_through_the_same_gates_and_audit(env):
    _, db = env
    assert call(env, "cm01", "POST", "/api/admin/bulk-send").status_code == 403
    done = call(env, "admin", "POST", "/api/admin/bulk-send", json={"limit": 15}).json()
    assert done["considered"] == 15 and done["sent"] + done["blocked_at_send"] == 15
    approvals = db.scalars(
        select(AuditLog).where(AuditLog.reason == "Bulk approval (demo shortcut)")
    ).all()
    assert len(approvals) == 15
    sends = db.scalars(
        select(AuditLog).where(
            AuditLog.action == "nba_sent", AuditLog.nba_id.in_({a.nba_id for a in approvals})
        )
    ).all()
    assert len(sends) == done["sent"] > 0


def test_only_admin_can_play_out_or_reset(env):
    for user in ("cm01", "rep01", "compliance1", "pat00001", "hcp0001"):
        assert call(env, user, "POST", "/api/admin/play-out").status_code == 403
        assert call(env, user, "POST", "/api/admin/reset").status_code == 403
    # There is no way to move the date.
    assert call(env, "admin", "POST", "/api/admin/advance", json={"days": 7}).status_code in (
        404,
        405,
    )
    assert call(env, "admin", "GET", "/api/clock").status_code in (404, 405)


def test_play_out_resolves_responses_and_real_days_bring_refills(env):
    _, db = env
    # Send a batch so there is something for the world to respond to.
    batch = db.scalars(
        select(Nba)
        .where(Nba.status == NbaStatus.READY_FOR_REVIEW)
        .order_by(Nba.priority.desc())
        .limit(40)
    ).all()
    for nba in batch:
        approve_and_send(env, "admin", nba)
    sent_ids = {n.id for n in batch}
    pending_before = db.scalars(
        select(Interaction).where(Interaction.outcome == Outcome.PENDING)
    ).all()
    assert len(pending_before) >= 40
    fills_before = len(db.scalars(select(MedicationFill.id)).all())

    # A week of real time has passed when the administrator plays the responses out.
    with clock.override(WEEK_LATER):
        result = call(env, "admin", "POST", "/api/admin/play-out")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["as_of_date"] == WEEK_LATER.isoformat()
        assert clock.simulated_through(db) == WEEK_LATER

        assert (
            db.scalars(select(Interaction).where(Interaction.outcome == Outcome.PENDING)).all()
            == []
        )
        responses = body["responses"]
        assert sum(v for k, v in responses.items() if k != "natural_fills") == len(pending_before)
        assert responses["natural_fills"] > 0
        assert len(db.scalars(select(MedicationFill.id)).all()) > fills_before
        # Nothing is recorded in the future.
        assert (
            db.scalars(select(MedicationFill).where(MedicationFill.fill_date > WEEK_LATER)).all()
            == []
        )

        # Outcomes were written back on the interactions the engine sent, and audited.
        outcomes = {
            i.outcome
            for i in db.scalars(select(Interaction).where(Interaction.nba_id.in_(sent_ids)))
        }
        assert Outcome.PENDING not in outcomes and len(outcomes) >= 2
        captured = db.scalars(
            select(AuditLog).where(
                AuditLog.action == "response_captured", AuditLog.actor == "simulator"
            )
        ).all()
        assert len(captured) == len(pending_before)

        # A new cycle exists for the date; the earlier unreviewed queue was superseded.
        new = db.scalars(select(Nba).where(Nba.cycle_id == body["cycle_id"])).all()
        assert new and all(n.as_of_date == WEEK_LATER for n in new)
        stale = db.scalars(
            select(Nba).where(
                Nba.cycle_id != body["cycle_id"], Nba.status == NbaStatus.READY_FOR_REVIEW
            )
        ).all()
        assert stale == []
        played = db.scalar(select(AuditLog).where(AuditLog.action == "responses_played_out"))
        assert played.actor == "admin"


def test_analytics_show_engine_versus_baseline_and_the_adherence_trend(env):
    assert call(env, "cm01", "GET", "/api/analytics/overview").status_code == 403
    with clock.override(WEEK_LATER):
        data = call(env, "admin", "GET", "/api/analytics/overview").json()
    assert data["as_of_date"] == WEEK_LATER.isoformat()

    sources = {(r["target_type"], r["source"]) for r in data["engagement"]["by_channel"]}
    assert {("PATIENT", "engine"), ("PATIENT", "baseline"), ("HCP", "baseline")} <= sources
    engine_all = next(
        r
        for r in data["engagement"]["by_channel"]
        if (r["target_type"], r["source"], r["channel"]) == ("PATIENT", "engine", "all")
    )
    assert engine_all["sent"] >= 10 and 0 <= engine_all["response_rate"] <= 1
    assert data["engagement"]["awaiting_response"] == 0
    fair = {r["source"]: r for r in data["engagement"]["like_for_like"]}
    assert fair["baseline"]["sent"] > 100 and fair["engine"]["sent"] >= 5
    assert fair["engine"]["sent"] <= engine_all["sent"]

    dates = sorted({t["as_of_date"] for t in data["adherence"]["trend"]})
    assert len(dates) == 2 and dates[-1] == data["as_of_date"]
    measures = {t["measure"] for t in data["adherence"]["current"]}
    assert measures == {"all", "diabetes", "hypertension", "cholesterol"}
    assert all(0 <= t["adherent_rate"] <= 1 for t in data["adherence"]["trend"])

    reasons = {r["code"] for r in data["recommendations"]["gate_reasons"]}
    assert "consent_missing" in reasons and any(r.startswith("mlr_") for r in reasons)
    assert len(data["cycles"]) >= 2 and data["recommendations"]["mix"]
    # Aggregates only: nothing in the payload names a person.
    assert "PAT_0" not in str(data) and "HCP_0" not in str(data)


def test_reset_restores_the_seeded_starting_point(env):
    _, db = env
    done = call(env, "admin", "POST", "/api/admin/reset", json={"patients": 60, "hcps": 12})
    assert done.status_code == 200, done.text
    assert done.json()["as_of_date"] == DEFAULT_AS_OF.isoformat()
    # Every session ended (account ids can change); the caller gets a new one.
    old = env[0].__dict__["_tokens"]["admin"]
    assert env[0].get("/api/auth/me", headers=cookie_header(nba_session=old)).status_code == 401
    env[0].__dict__["_tokens"].clear()
    me = env[0].get("/api/auth/me", headers=cookie_header(nba_session=session_of(done)))
    assert me.status_code == 200 and me.json()["role"] == "admin"
    assert len(db.scalars(select(Patient.patient_id)).all()) == 60
    assert db.scalars(select(Interaction).where(Interaction.source == "nba")).all() == []
    assert ready(db, "PAT_00001") is not None
    assert call(env, "pat00001", "GET", "/api/me/inbox").json() == []
