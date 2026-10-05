from dataclasses import replace
from datetime import date, datetime, timedelta
from math import log

import pytest
from conftest import seeded_session
from sqlalchemy import select

from app import pipeline
from app.core.engine_config import DEFAULTS
from app.features import engagement as eng
from app.features.adherence import compute_adherence, coverage_intervals
from app.features.population import load_population
from app.models import AdherenceSnapshot, FeatureSnapshot, Hcp, ModelVersion, Patient
from app.scoring import propensity
from app.scoring.risk import score_risk

D0 = date(2026, 1, 1)


def day(n: int) -> date:
    return D0 + timedelta(days=n)


# --- Adherence arithmetic (hand-computed cases) -------------------------------


def test_single_fill_fully_covered():
    a = compute_adherence([(day(0), 30)], day(0), as_of=day(29))
    assert (a.pdc, a.gap_days, a.n_fills) == (1.0, 0, 1)


def test_gap_after_supply_runs_out():
    # 30 covered days in a 60-day period; supply ran out on day 30, 29 days before day 59.
    a = compute_adherence([(day(0), 30)], day(0), as_of=day(59))
    assert a.pdc == 0.5
    assert a.gap_days == 29
    assert a.last_fill_date == day(0)


def test_early_refill_is_carried_forward_not_double_counted():
    fills = [(day(0), 30), (day(25), 30)]
    assert coverage_intervals(fills) == [(day(0), day(30)), (day(30), day(60))]
    a = compute_adherence(fills, day(0), as_of=day(59))
    assert a.pdc == 1.0
    assert a.mean_lateness == -5


def test_rolling_window_ignores_old_coverage():
    # Window is days 40..219 (180 days). Only days 200..219 are covered: 20 / 180.
    a = compute_adherence([(day(0), 30), (day(200), 30)], day(0), as_of=day(219), window_days=180)
    assert a.period_start == day(40)
    assert a.pdc == round(20 / 180, 4)
    assert a.mpr == round(30 / 180, 4)


def test_never_filled_prescription():
    a = compute_adherence([], day(0), as_of=day(20))
    assert a.never_filled and a.pdc == 0.0 and a.gap_days == 20 and a.last_fill_date is None


def test_future_fills_are_invisible():
    a = compute_adherence([(day(0), 30), (day(40), 30)], day(0), as_of=day(35))
    assert a.n_fills == 1 and a.gap_days == 5


def test_widening_gaps_give_positive_trend():
    fills, cursor = [], day(0)
    for late in [0, 0, 0, 5, 10, 15]:
        fills.append((cursor, 30))
        cursor += timedelta(days=30 + late)
    a = compute_adherence(fills, day(0), as_of=cursor)
    # lateness between fills = [0, 0, 0, 5, 10]; last three mean 5, earlier mean 0.
    assert a.late_trend == 5.0


# --- Risk score ---------------------------------------------------------------


def test_risk_score_is_sum_of_named_drivers():
    a = compute_adherence([(day(0), 30)], day(0), as_of=day(59))
    risk = score_risk(a, unresponsive_share=1.0, config=DEFAULTS)
    assert risk.score == sum(d["points"] for d in risk.drivers)
    assert {d["code"] for d in risk.drivers} == {"pdc_shortfall", "gap_days", "unresponsive"}
    assert risk.segment == "high"


def test_adherent_patient_is_low_risk():
    a = compute_adherence([(day(0), 30)], day(0), as_of=day(20))
    risk = score_risk(a, unresponsive_share=0.0, config=DEFAULTS)
    assert (risk.score, risk.segment, risk.drivers) == (0.0, "low", [])


# --- Pipeline on seeded data ---------------------------------------------------


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    db = seeded_session(tmp_path_factory.mktemp("models"))
    pop = load_population(db)
    stats = pipeline.refresh_features(db, pop)
    versions = db.scalars(select(ModelVersion).where(ModelVersion.is_active)).all()
    yield db, pop, stats, versions
    db.close()


def test_hero_risk_segments(seeded):
    db, *_ = seeded
    segment = {
        pid: db.get(Patient, pid).risk_segment
        for pid in ("PAT_00001", "PAT_00003", "PAT_00004", "PAT_00006")
    }
    assert segment == {
        "PAT_00001": "high",  # widening gap
        "PAT_00003": "low",  # steady
        "PAT_00004": "high",  # drifting, opted out
        "PAT_00006": "high",  # never filled
    }
    snap = db.scalar(select(AdherenceSnapshot).where(AdherenceSnapshot.patient_id == "PAT_00001"))
    assert snap.gap_days == 16 and snap.pdc < 0.80


def test_hcp_segments_and_affinity(seeded):
    db, pop, *_ = seeded
    cardiologist, fatigued = db.get(Hcp, "HCP_0001"), db.get(Hcp, "HCP_0003")
    assert cardiologist.segment.startswith("high_value")
    assert cardiologist.value_score > fatigued.value_score
    assert fatigued.segment.startswith("low_value")
    snap = db.get(FeatureSnapshot, ("HCP", "HCP_0002", pop.as_of))
    assert snap.features["topic_rates"]["measure"]["diabetes"] > eng.HCP_ENGAGE_PRIOR


def test_refresh_is_idempotent(seeded):
    db, pop, stats, _ = seeded
    before = len(db.scalars(select(AdherenceSnapshot)).all())
    assert pipeline.refresh_features(db, pop) == stats
    assert len(db.scalars(select(AdherenceSnapshot)).all()) == before


def test_training_rows_have_no_look_ahead(seeded):
    _, pop, *_ = seeded
    rows = eng.patient_training_rows(pop, 180)
    first_touch = {}
    for r in rows:
        first_touch.setdefault(r["target_id"], r)
    # Before a patient's first ever touch there is no outreach history to learn from.
    assert all(
        r["rate_all"] == pytest.approx(eng.PATIENT_ENGAGE_PRIOR)
        and r["n_channel"] == 0
        and r["days_since_last"] == eng.NO_HISTORY_DAYS
        for r in first_touch.values()
    )

    # Deleting everything after a cutoff must not change any row from before it.
    cutoff = datetime(2026, 6, 1)
    past = replace(
        pop,
        interactions={k: [i for i in v if i.int_ts < cutoff] for k, v in pop.interactions.items()},
        fills={k: [f for f in v if f[0] < cutoff.date()] for k, v in pop.fills.items()},
    )
    assert eng.patient_training_rows(past, 180) == [r for r in rows if r["ts"] < cutoff]
    hcp_rows = eng.hcp_training_rows(pop)
    assert eng.hcp_training_rows(past) == [r for r in hcp_rows if r["ts"] < cutoff]


def test_models_beat_no_model_baseline(seeded):
    *_, versions = seeded
    assert {v.name for v in versions} == {"patient_engage", "patient_fill", "hcp_engage"}
    for v in versions:
        assert v.is_active and v.metrics["auc"] > v.metrics["auc_baseline"], v.name
        assert v.metrics["brier"] < v.metrics["brier_base_rate"], v.name


def test_contributions_explain_the_prediction_exactly(seeded):
    db, pop, *_ = seeded
    spec = pipeline.PATIENT_FILL
    model = propensity.load_active(db, spec.name)
    row = eng.patient_training_rows(pop, 180)[0]
    p = propensity.predict(model, spec, [row])[0]
    parts = propensity.contributions(model, spec, row, top=1000)
    logit = model.named_steps["clf"].intercept_[0] + sum(c["contribution"] for c in parts)
    assert logit == pytest.approx(log(p / (1 - p)), abs=0.02)
    assert {c["feature"] for c in parts} <= set(spec.numeric + spec.categorical)
