"""The field-guide gaps closed in the vision-alignment pass: the HCP-patient seam (consented,
de-identified panel adherence in the engine and on HCP 360), the governed CRM hand-off, the
learning and quality-measure views, timing and scheduling, the synthetic holdout, the
scheduled cycle and the identity-resolution demonstration."""

import csv
import io
from datetime import datetime, timedelta

import pytest
from conftest import ApiClient, cookie_header, new_session, sign_in
from sqlalchemy import select

from app import cycle, panel_adherence, pipeline, scheduler, unify
from app.core import clock
from app.core.db import get_db
from app.core.engine_config import set_config, validate_value
from app.datagen.generate import GenConfig, generate
from app.engagement import delivery
from app.features.adherence import compute_persistence
from app.features.population import load_population
from app.integration import crm
from app.main import app
from app.models import (
    AuditLog,
    Consent,
    EngineCycle,
    Hcp,
    Interaction,
    Nba,
    Patient,
    RepHcp,
    User,
)
from app.models.enums import ConsentPurpose, NbaStatus, PatientOrigin, TargetType
from app.nba import holdout

PERSONAS = {
    "admin": "admin",
    "mlr": "compliance1",
    "rep": "rep01",
    "cm": "cm01",
    "patient": "pat00001",
    "hcp": "hcp0001",
}


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=57, n_patients=400, n_hcps=40, n_reps=4, n_care_managers=3))
    cycle.run(db, retrain=True)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    client = ApiClient(app)
    tokens = {role: sign_in(client, name) for role, name in PERSONAS.items()}
    yield client, db, tokens
    app.dependency_overrides.clear()
    db.close()


def call(env, role, method, path, **kw):
    client, _, tokens = env
    return client.request(method, path, headers=cookie_header(nba_session=tokens[role]), **kw)


def rep_hcp_ids(db, username="rep01") -> list[str]:
    uid = db.scalar(select(User.id).where(User.username == username))
    return list(db.scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == uid)))


# --- Seam: consent, suppression, engine, HCP 360 ------------------------------------------


def test_aggregate_consent_is_its_own_purpose_and_recorded_for_synthetic_patients(env):
    _, db, _ = env
    rows = db.scalars(
        select(Consent).where(Consent.purpose == ConsentPurpose.AGGREGATE_INSIGHTS)
    ).all()
    synthetic = db.scalars(select(Patient).where(Patient.origin == PatientOrigin.SYNTHETIC)).all()
    assert len({c.patient_id for c in rows}) == len(synthetic)
    share = sum(c.granted for c in rows) / len(rows)
    assert 0.6 < share < 0.9


def test_patient_can_switch_aggregate_statistics_on_and_off(env):
    listed = call(env, "patient", "GET", "/api/me/consents").json()
    assert "aggregate_insights" in {c["purpose"] for c in listed}
    off = call(
        env, "patient", "PUT", "/api/me/consents/aggregate-insights", json={"granted": False}
    )
    assert off.status_code == 200 and off.json()["granted"] is False
    on = call(env, "patient", "PUT", "/api/me/consents/aggregate-insights", json={"granted": True})
    assert on.json()["granted"] is True
    # Only the patient manages it.
    assert (
        call(env, "cm", "PUT", "/api/me/consents/aggregate-insights", json={"granted": True})
    ).status_code == 403


def test_only_consenting_patients_are_counted(env):
    _, db, _ = env
    settings = pipeline.engine_settings(db)
    today = clock.get_today(db)
    pts = panel_adherence.points_from_db(db, today, settings)
    counted = {p.patient_id for p in pts}
    refused = set(
        db.scalars(
            select(Consent.patient_id).where(
                Consent.purpose == ConsentPurpose.AGGREGATE_INSIGHTS, Consent.granted.is_(False)
            )
        )
    )
    granted_elsewhere = refused - set(
        db.scalars(
            select(Consent.patient_id).where(
                Consent.purpose == ConsentPurpose.AGGREGATE_INSIGHTS, Consent.granted
            )
        )
    )
    assert counted and not (counted & granted_elsewhere)
    # The population and direct readers agree.
    pop_points = panel_adherence.points_from_population(load_population(db), settings)
    assert {p.patient_id for p in pop_points} == counted


def _hcp(state: str | None, country: str | None = "US") -> Hcp:
    return Hcp(hcp_id="HCP_T", first_name="T", last_name="T", state=state, country=country)


def _point(pid: str, state: str, pdc: float, measure="cholesterol"):
    return panel_adherence.Point(pid, measure, state, pdc, pdc < 0.5)


SETTINGS = {
    "aggregate_min_cohort": 11,
    "pdc_threshold": 0.8,
    "panel_adherence_target": 0.8,
}


def test_small_groups_are_suppressed_and_never_carry_figures():
    pts = [_point(f"P{i}", "AZ", 0.5) for i in range(10)]
    out = panel_adherence.panel(_hcp("AZ"), set(), pts, SETTINGS)
    row = next(r for r in out["measures"] if r["measure"] == "cholesterol")
    assert row["suppressed"] and row["level"] is None
    assert "adherent_share" not in row and "patients" not in row
    # Eleven distinct patients make a group.
    pts.append(_point("P10", "AZ", 0.9))
    row = next(
        r
        for r in panel_adherence.panel(_hcp("AZ"), set(), pts, SETTINGS)["measures"]
        if r["measure"] == "cholesterol"
    )
    assert row["level"] == "area" and row["patients"] == 11 and row["below_target"]


def test_attributed_level_needs_both_panel_and_remainder_large_enough():
    own = {f"P{i}" for i in range(12)}
    pts = [_point(p, "AZ", 0.9) for p in own] + [_point(f"Q{i}", "AZ", 0.4) for i in range(5)]
    row = panel_adherence.panel(_hcp("AZ"), own, pts, SETTINGS)["measures"][2]
    # The rest of the area is only 5 patients: the HCP's own panel is not shown, the area is.
    assert row["level"] == "area"
    pts += [_point(f"R{i}", "AZ", 0.4) for i in range(6)]
    row = panel_adherence.panel(_hcp("AZ"), own, pts, SETTINGS)["measures"][2]
    assert row["level"] == "attributed" and row["patients"] == 12 and not row["below_target"]


def test_hcp_without_a_us_state_gets_no_area_figure():
    pts = [_point(f"P{i}", "AZ", 0.5) for i in range(30)]
    for hcp in (_hcp(None), _hcp("AZ", "GB")):
        rows = panel_adherence.panel(hcp, set(), pts, SETTINGS)["measures"]
        assert all(r["suppressed"] for r in rows)


def test_engine_explains_panel_signal_and_never_names_a_patient(env):
    _, db, _ = env
    latest = db.scalar(select(EngineCycle).order_by(EngineCycle.id.desc()))
    assert latest.stats.get("hcp_panel_signal", 0) > 0
    nbas = db.scalars(
        select(Nba).where(Nba.cycle_id == latest.id, Nba.target_type == TargetType.HCP)
    ).all()
    flagged = [n for n in nbas if any(r["code"] == "panel_adherence" for r in n.reason_codes)]
    assert flagged
    patient_ids = set(db.scalars(select(Patient.patient_id)))
    for n in flagged:
        reason = next(r for r in n.reason_codes if r["code"] == "panel_adherence")
        assert reason["kind"] == "context" and reason["patients"] >= 11
        assert not any(pid in n.rationale for pid in patient_ids)
        # Only adherence-support material is favoured, and the gates still decided eligibility.
        assert n.action in ("hcp_education", "program_info")


def test_hcp_360_shows_deidentified_panel_and_rep_scope_holds(env):
    _, db, _ = env
    mine = rep_hcp_ids(db)
    body = call(env, "rep", "GET", f"/api/hcps/{mine[0]}").json()
    panel = body["panel_adherence"]
    assert panel["min_cohort"] == 11 and len(panel["measures"]) == 3
    text = str(panel)
    assert "PAT_" not in text
    for row in panel["measures"]:
        assert row["suppressed"] or row["patients"] >= 11
    other = db.scalar(select(Hcp.hcp_id).where(Hcp.hcp_id.not_in(mine)))
    assert call(env, "rep", "GET", f"/api/hcps/{other}").status_code == 404
    assert call(env, "cm", "GET", f"/api/hcps/{mine[0]}").status_code == 403


# --- CRM hand-off --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def handed_off(env):
    """Approve and send a few recommendations of each audience."""
    _, db, _ = env
    today = clock.get_today(db)
    sent = []
    for target_type in (TargetType.HCP, TargetType.PATIENT):
        rows = db.scalars(
            select(Nba)
            .where(
                Nba.target_type == target_type,
                Nba.status == NbaStatus.READY_FOR_REVIEW,
                Nba.scheduled_for <= today,
            )
            .order_by(Nba.id)
        ).all()
        admin = db.scalar(select(User).where(User.username == "admin"))
        for nba in rows[:6]:
            nba.status, nba.reviewed_by_user_id = NbaStatus.APPROVED, admin.id
            nba.reviewed_ts = datetime.combine(today, datetime.min.time())
            try:
                delivery.send(db, nba, admin)
                sent.append(nba.id)
            except delivery.SendBlocked:
                pass
    db.commit()
    return sent


def test_export_field_map_and_permissions(env):
    fm = call(env, "admin", "GET", "/api/integration/crm/field-map").json()
    names = [f["name"] for f in fm["fields"]]
    assert names == crm.FIELD_NAMES and "record_id" in names
    for role in ("mlr", "patient", "hcp"):
        assert call(env, role, "GET", "/api/integration/crm/records").status_code == 403


def test_export_contains_only_handed_off_records_without_sensitive_fields(env, handed_off):
    _, db, _ = env
    body = call(env, "admin", "GET", "/api/integration/crm/records").json()
    assert body["count"] >= len(handed_off)
    statuses = {r["status"] for r in body["records"]}
    assert statuses <= {"approved", "sent", "responded"}
    names = {f"{p.first_name} {p.last_name}" for p in db.scalars(select(Patient))} | {
        p.first_name for p in db.scalars(select(Patient))
    }
    for r in body["records"]:
        assert set(r) == set(crm.FIELD_NAMES)
        assert r["record_id"] == f"NBA-{r['recommendation_id']}"
        if r["target_type"] == TargetType.PATIENT:
            assert r["content_title"] is None and r["hcp_npi"] is None
            assert r["outcome"] in (None, "engaged", "no_response", "declined")
            assert r["synthetic"] is True
    flat = str(body)
    assert not any(name in flat for name in names if len(name) > 3)
    for forbidden in ("rationale", "drug", "pdc", "body", "date_of_birth", "email"):
        assert forbidden not in crm.FIELD_NAMES


def test_export_is_scoped_to_the_reader(env, handed_off):
    _, db, _ = env
    mine = set(rep_hcp_ids(db))
    rep = call(env, "rep", "GET", "/api/integration/crm/records").json()
    assert all(r["target_type"] == "HCP" and r["target_id"] in mine for r in rep["records"])
    cm_id = db.scalar(select(User.id).where(User.username == "cm01"))
    from app.models import CareManagerPatient

    panel = set(
        db.scalars(
            select(CareManagerPatient.patient_id).where(
                CareManagerPatient.care_manager_user_id == cm_id
            )
        )
    )
    cm = call(env, "cm", "GET", "/api/integration/crm/records").json()
    assert all(r["target_type"] == "PATIENT" and r["target_id"] in panel for r in cm["records"])


def test_export_validates_parameters_and_is_audited(env, handed_off):
    _, db, _ = env
    bad = [
        "?target_type=DOCTOR",
        "?status=ready_for_review",
        "?since=2026-05-01&until=2026-01-01",
        "?since=2020-01-01&until=2026-01-01",
        "?limit=0",
        "?limit=99999",
    ]
    for query in bad:
        r = call(env, "admin", "GET", f"/api/integration/crm/records{query}")
        assert r.status_code == 422, query
    before = db.scalar(
        select(AuditLog.id).where(AuditLog.action == "crm_export").order_by(AuditLog.id.desc())
    )
    r = call(env, "admin", "GET", "/api/integration/crm/records?format=csv&target_type=HCP")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    reader = csv.reader(io.StringIO(r.text))
    assert next(reader) == crm.FIELD_NAMES
    log = db.scalar(
        select(AuditLog).where(AuditLog.action == "crm_export").order_by(AuditLog.id.desc())
    )
    assert log.id != before and log.detail["format"] == "csv" and log.detail["target_type"] == "HCP"
    assert "records" not in log.detail


def test_csv_cells_cannot_become_formulas():
    assert crm._cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert crm._cell("+1") == "'+1" and crm._cell("ok") == "ok" and crm._cell(None) == ""


# --- Timing and scheduling -----------------------------------------------------------------


def test_refill_reminder_scheduled_before_run_out():
    from datetime import date

    from app.features.adherence import compute_adherence
    from app.nba import rationale

    today = date(2026, 6, 1)
    fills = [(date(2026, 5, 6), 30)]  # runs out 5 Jun
    a = compute_adherence(fills, date(2026, 1, 1), today)
    runout = date(2026, 6, 5)
    day, note = rationale.patient_timing(a, today, runout, 3, "refill_nudge")
    assert day == date(2026, 6, 2) and note.startswith("Scheduled for")
    day, _ = rationale.patient_timing(a, today, runout, 3, "check_in")
    assert day == today
    day, _ = rationale.patient_timing(a, today, date(2026, 6, 3), 3, "refill_nudge")
    assert day == today


def test_scheduled_recommendation_cannot_be_sent_early(env):
    _, db, _ = env
    today = clock.get_today(db)
    later = db.scalar(
        select(Nba).where(
            Nba.status == NbaStatus.READY_FOR_REVIEW,
            Nba.scheduled_for > today,
            Nba.target_type == TargetType.HCP,
        )
    )
    assert later is not None, "the cycle schedules HCPs held back by the contact limits"
    assert "Scheduled for" in later.timing_note
    approved = call(env, "admin", "POST", f"/api/nba/{later.id}/approve", json={})
    assert approved.status_code == 200, approved.text
    early = call(env, "admin", "POST", f"/api/nba/{later.id}/send")
    assert early.status_code == 409 and early.json()["detail"]["code"] == "not_due"
    db.refresh(later)
    assert later.status == NbaStatus.APPROVED
    assert db.scalar(select(Interaction).where(Interaction.nba_id == later.id)) is None
    # On the day, every gate is checked again before it goes.
    with clock.override(later.scheduled_for):
        sent = call(env, "admin", "POST", f"/api/nba/{later.id}/send")
    assert sent.status_code in (200, 409)
    if sent.status_code == 409:
        assert sent.json()["detail"]["code"] == "blocked"


# --- Learning, measures, holdout -----------------------------------------------------------


def test_learning_view_reports_sample_sizes_and_hides_small_rates(env, handed_off):
    body = call(env, "admin", "GET", "/api/analytics/learning?audience=PATIENT").json()
    assert body["rows"] and body["min_n"] == 20
    for row in body["rows"]:
        if row["n"] < 20:
            assert row["response"]["rate"] is None and row["fill"]["rate"] is None
        else:
            lo, hi = row["response"]["ci"]
            assert lo <= row["response"]["rate"] <= hi
    hcp = call(env, "admin", "GET", "/api/analytics/learning?audience=HCP&segment_by=specialty")
    assert hcp.status_code == 200 and hcp.json()["segment_by"] == "specialty"
    assert call(env, "admin", "GET", "/api/analytics/learning?segment_by=zip").status_code == 422
    assert call(env, "rep", "GET", "/api/analytics/learning").status_code == 403
    models = call(env, "mlr", "GET", "/api/analytics/models").json()
    assert {m["name"] for m in models["models"]} and "calibration" in models


def test_quality_measures_scope_and_suppression(env):
    _, db, _ = env
    everyone = call(env, "admin", "GET", "/api/analytics/measures").json()
    assert everyone["scope"] == "all" and len(everyone["measures"]) == 3
    assert any(not m["current"]["suppressed"] for m in everyone["measures"])
    panel = call(env, "cm", "GET", "/api/analytics/measures").json()
    assert panel["scope"] == "panel" and panel["patients"] < everyone["patients"]
    for m in panel["measures"] + everyone["measures"]:
        for figure in [m["current"], m["contacted"], m["not_contacted"], *m["persistence"]]:
            assert figure["suppressed"] == (figure["n"] < 11)
            assert figure["suppressed"] == (figure["rate"] is None)
    assert call(env, "patient", "GET", "/api/analytics/measures").status_code == 403
    assert "not_modelled" in everyone and everyone["not_modelled"]


def test_persistence_definition():
    from datetime import date

    d = date(2026, 1, 1)
    assert compute_persistence([], d).first_fill is None
    p = compute_persistence([(d, 30), (d + timedelta(days=100), 30)], d + timedelta(days=200), 60)
    assert p.discontinued and p.days_persistent == 30
    p = compute_persistence([(d, 30), (d + timedelta(days=80), 30)], d + timedelta(days=120), 60)
    assert not p.discontinued and p.days_persistent == 110


def test_patient_360_shows_individual_persistence(env):
    body = call(env, "cm", "GET", "/api/patients").json()
    pid = body["items"][0]["patient_id"]
    profile = call(env, "cm", "GET", f"/api/patients/{pid}").json()
    assert all("persistence" in t for t in profile["therapies"])


def test_holdout_is_synthetic_only_deterministic_and_off_by_default(env):
    _, db, _ = env
    config = pipeline.engine_settings(db)["holdout"]
    assert config["enabled"] is False
    real = Patient(patient_id="PAT_R999999", origin=PatientOrigin.SELF_REGISTERED)
    synthetic = db.scalars(select(Patient).where(Patient.origin == PatientOrigin.SYNTHETIC)).all()
    on = {"enabled": True, "share": 0.5}
    assert not holdout.in_holdout(real, on)
    held = [p for p in synthetic if holdout.in_holdout(p, on)]
    assert 0.35 < len(held) / len(synthetic) < 0.65
    assert held == [p for p in synthetic if holdout.in_holdout(p, on)]
    assert not any(holdout.in_holdout(p, config) for p in synthetic)


def test_holdout_cycle_skips_held_out_patients_and_comparison_reports(env):
    _, db, _ = env
    set_config(db, "holdout", {"enabled": True, "share": 0.2})
    result = cycle.run(db)
    db.commit()
    try:
        assert result.stats["patient_holdout"] > 0
        held = {
            p.patient_id for p in db.scalars(select(Patient)) if holdout.assigned(p, {"share": 0.2})
        }
        new = db.scalars(
            select(Nba).where(Nba.cycle_id == result.cycle.id, Nba.target_type == "PATIENT")
        ).all()
        assert not {n.target_id for n in new} & held
        body = call(env, "admin", "GET", "/api/analytics/holdout").json()
        assert body["started"] and body["status"] in ("insufficient", "ready")
        assert "simulation" in body["basis"]
    finally:
        set_config(db, "holdout", {"enabled": False, "share": 0.1})
        db.commit()


def test_new_settings_are_validated():
    assert validate_value("holdout", {"enabled": True, "share": 1.5})
    assert validate_value("holdout", {"enabled": True}) is not None
    assert validate_value("auto_cycle", {"enabled": True, "hour_utc": 24})
    assert validate_value("auto_cycle", {"enabled": True, "hour_utc": 5}) is None
    assert validate_value("aggregate_min_cohort", 3)
    assert validate_value("aggregate_min_cohort", 11) is None


def test_scheduled_cycle_runs_once_a_day_only_when_enabled(env):
    _, db, _ = env
    today = clock.get_today(db)
    morning = datetime.combine(today, datetime.min.time()).replace(hour=7)
    assert scheduler.run_if_due(db, morning) is None  # off by default
    set_config(db, "auto_cycle", {"enabled": True, "hour_utc": 6})
    db.commit()
    try:
        # A cycle already ran today (the fixture's): none is added.
        assert scheduler.run_if_due(db, morning) is None
        tomorrow = morning + timedelta(days=1)
        assert scheduler.run_if_due(db, tomorrow.replace(hour=5)) is None
        with clock.override(today + timedelta(days=1)):
            cycle_id = scheduler.run_if_due(db, tomorrow)
            assert cycle_id is not None
            assert scheduler.run_if_due(db, tomorrow.replace(hour=9)) is None
        log = db.scalar(select(AuditLog).where(AuditLog.action == "cycle_scheduled"))
        assert log is not None and log.entity_id == str(cycle_id)
    finally:
        set_config(db, "auto_cycle", {"enabled": False, "hour_utc": 6})
        db.commit()


# --- Unification ---------------------------------------------------------------------------


def test_identity_resolution_matches_truth_and_never_merges_ambiguity(env):
    _, db, _ = env
    hcps, matches = unify.resolve(db)
    for m in matches:
        if m.status == "merged":
            assert m.hcp_id == m.record.truth
        if m.status == "ambiguous":
            assert m.hcp_id is None and len(m.candidates) > 1
        if m.status == "unmatched":
            assert m.hcp_id is None and m.record.truth is None
    assert sum(m.status == "merged" for m in matches) > len(hcps)
    # Deterministic.
    assert [m.status for m in unify.resolve(db)[1]] == [m.status for m in matches]


def test_unification_endpoint_admin_only_and_hcp_sources_hide_other_profiles(env):
    _, db, _ = env
    assert call(env, "rep", "GET", "/api/unification").status_code == 403
    body = call(env, "admin", "GET", "/api/unification").json()
    assert body["records"] and body["rules"][0]["rule"] == "R1"
    mine = rep_hcp_ids(db)
    for hcp_id in mine:
        sources = call(env, "rep", "GET", f"/api/hcps/{hcp_id}").json()["sources"]
        for item in sources["review"]:
            assert item["candidates"] == []
        assert all(item["hcp_id"] == hcp_id for item in sources["merged"])
