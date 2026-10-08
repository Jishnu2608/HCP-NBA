"""Role-specific insights (`app.insights`, `/api/insights/*`): figures come from the records,
respect each caller's scope, change as soon as the records change, never claim more than the
data supports, and streaks follow their written definitions."""

from datetime import date, datetime, timedelta

import pytest
from conftest import ApiClient, as_user, auth, login, new_session
from sqlalchemy import func, select
from test_hcp_journey import ENDO, onboard_hcp, patient_with_diabetes, route

from app import cycle
from app.core import clock
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.insights import streaks, typical
from app.main import app
from app.models import (
    CareManagerPatient,
    CareRequest,
    Content,
    HcpTask,
    RepHcp,
    User,
)
from app.models.tables import utcnow


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=97, n_patients=160, n_hcps=40, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


@pytest.fixture(scope="module")
def hcp(env):
    client, _ = env
    s = onboard_hcp(client, "insights.hcp@example.org", [ENDO], name="Ines Ward")
    return {"h": as_user(s), "hcp_id": s["user"]["hcp_id"]}


def get(client, path, headers):
    r = client.get(path, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def uid(db, username) -> int:
    return db.scalar(select(User.id).where(User.username == username))


# --- Streak definitions --------------------------------------------------------------------


def test_medicine_on_hand_streak_counts_covered_days_up_to_today():
    today = date(2026, 10, 8)
    covered = [(today - timedelta(days=4), today + timedelta(days=10))]
    assert streaks.medicine_on_hand([(today - timedelta(days=30), covered)], today)["value"] == 5
    # A medication with no supply today breaks it at once.
    gap = [(today - timedelta(days=30), today - timedelta(days=1))]
    assert streaks.medicine_on_hand([(today - timedelta(days=60), gap)], today)["value"] == 0
    assert streaks.medicine_on_hand([], today) is None
    # Before a medication started it is not required.
    late = [(today - timedelta(days=2), [(today - timedelta(days=2), today + timedelta(days=5))])]
    assert streaks.medicine_on_hand(late, today)["value"] == 3


def test_no_overdue_streak_stops_at_the_first_overdue_day():
    today = date(2026, 10, 8)
    created = datetime(2026, 9, 20, 9)
    # Due 1 Oct, closed 4 Oct: overdue at the end of 2 and 3 Oct (not on its due day).
    items = [(date(2026, 10, 1), created, datetime(2026, 10, 4, 10))]
    assert streaks.no_overdue_days(items, today)["value"] == 5  # 4..8 Oct
    # Still open: overdue today, the streak is 0.
    assert streaks.no_overdue_days([(date(2026, 10, 7), created, None)], today)["value"] == 0
    # Nothing dated: nothing to measure.
    assert streaks.no_overdue_days([], today) is None


def test_on_time_and_answered_streaks_count_back_from_the_latest():
    d = datetime(2026, 10, 1, 9)
    done = [(d, date(2026, 10, 1)), (d + timedelta(days=2), date(2026, 10, 1)),
            (d + timedelta(days=3), date(2026, 10, 5))]  # fmt: skip
    assert streaks.on_time_completions(done)["value"] == 1  # latest on time, then one late
    assert streaks.on_time_completions([]) is None
    outcomes = [(d, True), (d + timedelta(hours=1), False), (d + timedelta(hours=2), True)]
    assert streaks.answered_in_a_row(outcomes)["value"] == 1
    assert streaks.answered_in_a_row([(d, True), (d + timedelta(1), True)])["value"] == 2


def test_typical_needs_three_values():
    assert typical([1.0, 2.0])["median"] is None and typical([1.0, 2.0])["n"] == 2
    assert typical([1.0, 2.0, 9.0])["median"] == 2.0


# --- Patient --------------------------------------------------------------------------------


def test_patient_sees_supply_on_hand_and_consultation_progress(env, hcp):
    client, db = env
    p = patient_with_diabetes(client, db, "insights.patient@example.org")
    data = get(client, "/api/insights/patient", p["me"])
    (med,) = data["medicines"]
    assert len(med["days"]) == data["window_days"] == 30
    # Last refill 5 days ago with 30 days of supply: the last 6 days are covered.
    assert med["days"][-1]["state"] == "covered" and data["streak"]["value"] >= 6
    assert "risk" not in str(data)
    # Progress: requested, with the care manager.
    request = next(r for r in get(client, "/api/me/health", p["me"])["requests"]
                   if r["id"] == p["request"])  # fmt: skip
    assert request["progress"]["current"] == "care_manager"
    # Other roles cannot read a patient's insights.
    assert client.get("/api/insights/patient", headers=p["cm"]).status_code == 403
    assert client.get("/api/insights/patient", headers=hcp["h"]).status_code == 403


# --- HCP, care manager and patient follow one consultation ---------------------------------


def test_one_consultation_moves_every_view_without_delay(env, hcp):
    client, db = env
    p = patient_with_diabetes(client, db, "insights.flow@example.org")
    before = get(client, "/api/insights/hcp", hcp["h"])
    waiting_before = before["workload"]["segments"][0]["value"]
    assert route(client, p, hcp["hcp_id"]).status_code == 200
    r = db.get(CareRequest, p["request"])
    assert r.routed_at is not None and r.responded_at is None

    # The HCP's workload and the care manager's waiting list change at once.
    after = get(client, "/api/insights/hcp", hcp["h"])
    assert after["workload"]["segments"][0]["value"] == waiting_before + 1
    cm = get(client, "/api/insights/care", p["cm"])
    assert any(w["request_id"] == p["request"] for w in cm["waiting_with_hcp"])
    progress = next(x for x in get(client, "/api/me/health", p["me"])["requests"]
                    if x["id"] == p["request"])["progress"]  # fmt: skip
    assert progress["current"] == "hcp"
    assert next(s for s in progress["steps"] if s["key"] == "hcp")["date"] is not None

    answered = client.post(
        f"/api/me/consultations/{p['request']}/respond",
        json={"response": "advice", "message": "Keep the dose; recheck in 3 months."},
        headers=hcp["h"],
    )
    assert answered.status_code == 200
    r = db.get(CareRequest, p["request"])
    assert r.responded_at is not None
    data = get(client, "/api/insights/hcp", hcp["h"])
    times = data["response_times"]
    assert any(i["request_id"] == p["request"] for i in times["items"])
    # Too few answers for a typical time: no median is claimed.
    assert times["summary"]["n"] < 3 and times["summary"]["median"] is None
    assert data["streak"]["value"] >= 1
    cm = get(client, "/api/insights/care", p["cm"])
    assert not any(w["request_id"] == p["request"] for w in cm["waiting_with_hcp"])

    closed = client.patch(
        f"/api/care/requests/{p['request']}",
        json={"status": "closed", "resolution": "Advice shared with the patient."},
        headers=p["cm"],
    )
    assert closed.status_code == 200, closed.text
    assert db.get(CareRequest, p["request"]).closed_at is not None
    progress = next(x for x in get(client, "/api/me/health", p["me"])["requests"]
                    if x["id"] == p["request"])["progress"]  # fmt: skip
    assert progress["current"] is None
    assert all(s["state"] == "done" for s in progress["steps"])


def test_a_decline_breaks_the_hcp_streak_and_skips_steps_on_withdrawal(env, hcp):
    client, db = env
    p = patient_with_diabetes(client, db, "insights.decline@example.org")
    assert route(client, p, hcp["hcp_id"]).status_code == 200
    client.post(
        f"/api/me/consultations/{p['request']}/respond",
        json={"response": "decline", "note_to_care_team": "Not my area this week."},
        headers=hcp["h"],
    )
    assert get(client, "/api/insights/hcp", hcp["h"])["streak"]["value"] == 0
    # Withdrawn by the care manager without an answer: the answer step is skipped.
    closed = client.patch(
        f"/api/care/requests/{p['request']}",
        json={"status": "closed", "resolution": "Handled at the clinic."},
        headers=p["cm"],
    )
    assert closed.status_code == 200, closed.text
    progress = next(x for x in get(client, "/api/me/health", p["me"])["requests"]
                    if x["id"] == p["request"])["progress"]  # fmt: skip
    assert next(s for s in progress["steps"] if s["key"] == "answered")["state"] == "skipped"


# --- Care manager ---------------------------------------------------------------------------


def test_care_manager_sees_only_their_panel(env):
    client, db = env
    data = get(client, "/api/insights/care", auth(client, "cm01"))
    panel = set(
        db.scalars(
            select(CareManagerPatient.patient_id).where(
                CareManagerPatient.care_manager_user_id == uid(db, "cm01")
            )
        )
    )
    points = data["attention"]["points"]
    assert points and {pt["patient_id"] for pt in points} <= panel
    assert all(0 <= pt["risk_score"] <= 100 and pt["days_since_contact"] >= 0 for pt in points)
    assert client.get("/api/insights/care", headers=auth(client, "rep01")).status_code == 403


def test_follow_up_load_and_streak_follow_due_dates(env):
    client, db = env
    cm_id = uid(db, "cm01")
    pid = db.scalar(
        select(CareManagerPatient.patient_id).where(
            CareManagerPatient.care_manager_user_id == cm_id
        )
    )
    today = clock.get_today(db)
    before = get(client, "/api/insights/care", auth(client, "cm01"))["follow_ups"]
    overdue = CareRequest(
        patient_id=pid, type="follow_up", status="open", owner_user_id=cm_id,
        due_date=today - timedelta(days=2), created_at=utcnow() - timedelta(days=5),
    )  # fmt: skip
    db.add(overdue)
    db.commit()
    data = get(client, "/api/insights/care", auth(client, "cm01"))
    seg = {s["key"]: s["value"] for s in data["follow_ups"]["segments"]}
    old = {s["key"]: s["value"] for s in before["segments"]}
    assert seg["overdue"] == old["overdue"] + 1
    assert data["streak"]["value"] == 0
    db.delete(overdue)
    db.commit()


def test_patient_360_carries_risk_history_for_staff(env):
    client, db = env
    pid = db.scalar(select(CareManagerPatient.patient_id))
    cm = db.get(User, db.scalar(
        select(CareManagerPatient.care_manager_user_id).where(CareManagerPatient.patient_id == pid)
    ))  # fmt: skip
    data = get(client, f"/api/patients/{pid}", auth(client, cm.username))
    assert isinstance(data["risk_history"], list) and data["risk_history"]
    assert all("risk_score" in h for h in data["risk_history"])


# --- MLR ------------------------------------------------------------------------------------


def test_content_insights_are_for_reviewers_only(env):
    client, db = env
    data = get(client, "/api/insights/content", auth(client, "compliance1"))
    pending = db.scalar(
        select(func.count()).select_from(Content).where(Content.mlr_status == "pending")
    )
    assert len(data["waiting"]) == pending
    assert all(e["days_remaining"] <= data["expiry_window_days"] for e in data["expiring"])
    assert [p["key"] for p in data["concerns"]["perspectives"]] == [
        "medical",
        "legal",
        "regulatory",
    ]
    assert data["turnaround"]["summary"]["n"] == len(data["turnaround"]["items"])
    for who in ("admin", "rep01", "cm01"):
        assert client.get("/api/insights/content", headers=auth(client, who)).status_code == 403


# --- Representative -------------------------------------------------------------------------


def test_rep_insights_cover_assigned_hcps_only(env):
    client, db = env
    data = get(client, "/api/insights/rep", auth(client, "rep01"))
    assigned = set(db.scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == uid(db, "rep01"))))
    windows = data["contact_windows"]
    shown = {w["hcp_id"] for w in windows["items"]} | {w["hcp_id"] for w in windows["not_open"]}
    assert shown == assigned
    assert sum(s["value"] for s in data["engagement"]["segments"]) == len(assigned)
    assert len(data["upcoming"]["days"]) == 14
    # Rates stay hidden below the minimum sample.
    sent = next(s["value"] for s in data["activity"]["stages"] if s["key"] == "sent")
    assert (data["activity"]["response_rate"] is None) == (sent < data["activity"]["min_rate_n"])
    for who in ("cm01", "compliance1"):
        assert client.get("/api/insights/rep", headers=auth(client, who)).status_code == 403


def test_rep_on_time_streak_uses_completed_tasks(env):
    client, db = env
    rep_id = uid(db, "rep01")
    hcp_id = db.scalar(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == rep_id))
    now = utcnow()
    task = HcpTask(
        hcp_id=hcp_id, kind="follow_up", status="done", owner_user_id=rep_id,
        due_date=now.date() + timedelta(days=1), completed_at=now, created_at=now, updated_at=now,
    )  # fmt: skip
    db.add(task)
    db.commit()
    assert get(client, "/api/insights/rep", auth(client, "rep01"))["streak"]["value"] >= 1
    late = HcpTask(
        hcp_id=hcp_id, kind="follow_up", status="done", owner_user_id=rep_id,
        due_date=now.date() - timedelta(days=3), completed_at=now + timedelta(seconds=1),
        created_at=now, updated_at=now,
    )  # fmt: skip
    db.add(late)
    db.commit()
    assert get(client, "/api/insights/rep", auth(client, "rep01"))["streak"]["value"] == 0
    db.delete(task)
    db.delete(late)
    db.commit()


# --- Administrator --------------------------------------------------------------------------


def test_operations_count_failures_without_health_data(env):
    client, _ = env
    before = get(client, "/api/insights/operations", auth(client, "admin"))
    failed = next(s for s in before["failures"] if s["key"] == "login_failed")["total"]
    assert login(client, "cm01@nba.demo", "wrong-password-1").status_code == 401
    data = get(client, "/api/insights/operations", auth(client, "admin"))
    assert next(s for s in data["failures"] if s["key"] == "login_failed")["total"] == failed + 1
    assert all(len(s["points"]) == 14 for s in data["failures"])
    assert data["workload"] and all(w["open_work"] >= 0 for w in data["workload"])
    text = str(data["workload"])
    assert "PAT_" not in text and "HCP_" not in text
    for who in ("cm01", "rep01", "compliance1"):
        assert client.get("/api/insights/operations", headers=auth(client, who)).status_code == 403
