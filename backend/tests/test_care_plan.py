"""Health goal plans set by the care manager; readings, check-ins and coins recorded by the
patient (coins enforced on the server, one per qualifying day); the opt-in board shows only
aliases; the HCP's representative tab is counted apart from patient work; and the new chart
figures agree with their source records."""

from datetime import timedelta

import pytest
from conftest import ApiClient, auth, new_session
from sqlalchemy import func, select
from test_hcp_journey import patient_with_diabetes

from app import cycle
from app.core import clock
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import AuditLog, CoinLedger, Content, User
from app.models.tables import utcnow

BP = {"key": "blood_pressure", "targets": {"systolic": {"low": 90, "high": 130}}}
GLUCOSE = {"key": "blood_glucose"}


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=61, n_patients=160, n_hcps=40, n_reps=3, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


@pytest.fixture(scope="module")
def patient(env):
    client, db = env
    return patient_with_diabetes(client, db, "plan.patient@example.org")


def code(r) -> str:
    return r.json()["detail"]["code"]


def plan_put(client, p, body):
    return client.put(f"/api/care/patients/{p['pid']}/plan", json=body, headers=p["cm"])


def test_no_plan_shows_nothing_and_refuses_readings(env, patient):
    client, _ = env
    mine = client.get("/api/me/plan", headers=patient["me"]).json()
    assert mine["plan"] is None and mine["calendar"] is None and mine["readings"] == []
    assert mine["has_care_manager"] is True and mine["care_managers"]
    r = client.post(
        "/api/me/readings",
        json={"measure": "blood_pressure", "values": {"systolic": 120, "diastolic": 80}},
        headers=patient["me"],
    )
    assert r.status_code == 409 and code(r) == "no_plan"
    assert client.get("/api/me/coins", headers=patient["me"]).json()["balance"] == 0


def test_care_manager_sets_a_valid_plan_only(env, patient):
    client, _ = env
    too_many = plan_put(client, patient, {"measures": [BP], "checkin_quota": 2})
    assert too_many.status_code == 422 and code(too_many) == "invalid_quota"
    inverted = {"key": "blood_pressure", "targets": {"systolic": {"low": 140, "high": 120}}}
    bad_target = plan_put(client, patient, {"measures": [inverted], "checkin_quota": 1})
    assert bad_target.status_code == 422 and code(bad_target) == "invalid_target"
    unknown = plan_put(client, patient, {"measures": [{"key": "mood"}], "checkin_quota": 1})
    assert unknown.status_code == 422
    empty = plan_put(client, patient, {"measures": [], "checkin_quota": 1})
    assert code(empty) == "empty_plan"
    ok = plan_put(
        client, patient, {"measures": [BP, GLUCOSE], "medication_check": True, "checkin_quota": 2}
    )
    assert ok.status_code == 200, ok.text
    plan = ok.json()["plan"]
    assert plan["items"] == ["blood_pressure", "blood_glucose", "medication"]
    assert plan["measures"][0]["targets"]["systolic"] == {"low": 90, "high": 130}
    assert plan["measures"][1]["targets"] == {}  # no target: shown without a band
    # The patient sees it at once, and cannot change it.
    mine = client.get("/api/me/plan", headers=patient["me"]).json()
    assert mine["plan"]["checkin_quota"] == 2
    denied = client.put(
        f"/api/care/patients/{patient['pid']}/plan",
        json={"measures": [BP], "checkin_quota": 1},
        headers=patient["me"],
    )
    assert denied.status_code == 403


def test_other_roles_and_other_panels_cannot_touch_the_plan(env, patient):
    client, db = env
    owner = db.scalar(select(User.username).where(User.id.in_(
        select(User.id).where(User.role == "care_manager")
    )))  # fmt: skip
    others = [u for u in ("cm01", "cm02") if u != owner]
    for who in others + ["rep01", "hcp0001"]:
        r = client.get(f"/api/care/patients/{patient['pid']}/plan", headers=auth(client, who))
        assert r.status_code in (403, 404), (who, r.status_code)


def test_checkins_count_each_plan_item_once_and_award_one_coin(env, patient):
    client, db = env
    me = patient["me"]

    def reading(measure, values):
        return client.post(
            "/api/me/readings", json={"measure": measure, "values": values}, headers=me
        )

    first = reading("blood_pressure", {"systolic": 128, "diastolic": 82})
    assert first.status_code == 201, first.text
    assert first.json()["progress"]["done"] == ["blood_pressure"]
    assert first.json()["coins"]["balance"] == 0
    # The same item again: recorded, but not a second check-in.
    again = reading("blood_pressure", {"systolic": 126, "diastolic": 80})
    assert again.json()["progress"]["done"] == ["blood_pressure"]
    # Out of range, not in the plan, in the future: refused.
    assert code(reading("blood_pressure", {"systolic": 400, "diastolic": 80})) == "invalid_reading"
    assert code(reading("weight", {"weight": 80})) == "not_in_plan"
    future = client.post(
        "/api/me/readings",
        json={"measure": "blood_glucose", "values": {"glucose": 110},
              "taken_at": (utcnow() + timedelta(hours=2)).isoformat()},
        headers=me,
    )  # fmt: skip
    assert code(future) == "future_reading"
    # The second distinct item meets the quota of 2: one coin, awarded by the server.
    med = client.post("/api/me/checkins/medication", headers=me)
    assert med.status_code == 201
    assert med.json()["progress"]["met"] and med.json()["progress"]["coin_today"]
    assert med.json()["coins"]["balance"] == 1
    # More check-ins the same day never earn more.
    client.post("/api/me/checkins/medication", headers=me)
    reading("blood_glucose", {"glucose": 104})
    pid = patient["pid"]
    assert (
        db.scalar(select(func.count()).select_from(CoinLedger).where(CoinLedger.patient_id == pid))
        == 1
    )
    coins = client.get("/api/me/coins", headers=me).json()
    assert coins["balance"] == 1 and coins["next_badge"] == 7
    assert [b["earned"] for b in coins["badges"]] == [False, False, False, False]
    # The charts read what was recorded: the 2 valid BP readings, with the plan's targets.
    mine = client.get("/api/me/plan", headers=me).json()
    bp = next(s for s in mine["readings"] if s["key"] == "blood_pressure")
    assert len(bp["readings"]) == 2 and bp["targets"]["systolic"]["high"] == 130
    today = next(
        d for d in mine["calendar"]["days"] if d["date"] == clock.get_today(db).isoformat()
    )
    assert today["count"] == 3 and today["coin"] is True


def test_board_is_opt_in_and_shows_aliases_only(env, patient):
    client, _ = env
    me = patient["me"]
    board = client.get("/api/me/board", headers=me).json()
    assert board["opted_in"] is False and not any(e["you"] for e in board["entries"])
    joined = client.put("/api/me/board", json={"opted_in": True}, headers=me).json()
    assert joined["opted_in"] and joined["alias"]
    mine = joined["you"]
    assert mine and mine["coins"] == 1 and mine["alias"] == joined["alias"]
    text = str(joined)
    assert patient["pid"] not in text and "Ria" not in text and "diabetes" not in text.lower()
    left = client.put("/api/me/board", json={"opted_in": False}, headers=me).json()
    assert left["opted_in"] is False and left["you"] is None


def test_hcp_counts_keep_representative_messages_apart(env):
    client, _ = env
    counts = client.get("/api/me/attention", headers=auth(client, "hcp0001")).json()
    assert "consultations" in counts
    assert "messages" not in counts
    assert "rep_messages" in counts


def test_new_chart_figures_match_their_records(env, patient):
    client, db = env
    care = client.get("/api/insights/care", headers=patient["cm"]).json()
    open_now = care["burn_down"]["days"][-1]["open"]
    tiers = care["risk_caseload"]["tiers"]
    assert sum(t["open"] + t["overdue"] for t in tiers) == open_now
    content = client.get("/api/insights/content", headers=auth(client, "compliance1")).json()
    assert sum(p["value"] for p in content["pipeline"]) == db.scalar(
        select(func.count()).select_from(Content)
    )
    ops = client.get("/api/insights/operations", headers=auth(client, "admin")).json()
    since = utcnow().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=29)
    assert ops["audit_heatmap"]["total"] == db.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.ts >= since)
    )
    rep = client.get("/api/insights/rep", headers=auth(client, "rep01")).json()
    week = rep["week_outcomes"]
    assert sum(sum(d[g["key"]] for g in week["groups"]) for d in week["days"]) == week["total"]
    hcp = client.get("/api/insights/hcp", headers=auth(client, "hcp0001")).json()
    assert len(hcp["per_week"]["weeks"]) == 8
    by = hcp["by_product"]
    assert sum(p["delivered"] for p in by["products"]) == by["delivered"]
    for p in by["products"]:
        assert p["interested"] + p["answered"] + p["declined"] + p["waiting"] == p["delivered"]
