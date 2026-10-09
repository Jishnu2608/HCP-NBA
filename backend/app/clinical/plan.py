"""A patient's health goal plan and what the patient records against it.

The care manager sets the plan: which measurements to track (from the catalogue below),
optional target ranges per component, whether the daily "took my medicines" confirmation is
part of it, and the daily check-in quota. The patient records readings and completes
check-ins; they never change the plan or its targets.

Check-ins: one per plan item per day (a tracked measurement logged that day, or the medicines
confirmation). Logging the same item twice never counts twice, so the quota cannot be met by
repetition, and items outside the plan do not count.

Coins: one per day on which the quota was met, awarded here (never by the client) and
protected by a unique (patient, day) row. Coins reward completing the plan's check-ins on a
day; they say nothing about the readings themselves and never imply a medical benefit.

Board: opt-in only, under a random healthcare-themed alias with the country; never a name,
reading or condition.
"""

import random
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import audit
from app.auth.errors import AuthError
from app.core import clock
from app.models import (
    CarePlan,
    CarePlanMeasure,
    CheckIn,
    CoinLedger,
    HealthReading,
    LeaderboardProfile,
    Patient,
    User,
)
from app.models.tables import utcnow

# The measurements a plan can track. Add an entry here to support a new measurement; the
# API, the forms and the charts read this catalogue and need no change. `min` / `max` bound
# what can be recorded (plausibility, not a clinical target).
MEASURES: dict[str, dict] = {
    "blood_pressure": {
        "label": "Blood pressure",
        "components": [
            {
                "key": "systolic",
                "label": "Systolic",
                "unit": "mmHg",
                "min": 50,
                "max": 260,
                "step": 1,
            },
            {
                "key": "diastolic",
                "label": "Diastolic",
                "unit": "mmHg",
                "min": 30,
                "max": 160,
                "step": 1,
            },
        ],
    },
    "blood_glucose": {
        "label": "Blood glucose",
        "components": [
            {
                "key": "glucose",
                "label": "Glucose",
                "unit": "mg/dL",
                "min": 20,
                "max": 600,
                "step": 1,
            }
        ],
    },
    "peak_flow": {
        "label": "Peak flow",
        "components": [
            {
                "key": "peak_flow",
                "label": "Peak flow",
                "unit": "L/min",
                "min": 50,
                "max": 900,
                "step": 5,
            }
        ],
    },
    "oxygen_saturation": {
        "label": "Oxygen saturation",
        "components": [
            {"key": "spo2", "label": "SpO2", "unit": "%", "min": 70, "max": 100, "step": 1}
        ],
    },
    "weight": {
        "label": "Weight",
        "components": [
            {"key": "weight", "label": "Weight", "unit": "kg", "min": 20, "max": 350, "step": 0.1}
        ],
    },
    "heart_rate": {
        "label": "Heart rate",
        "components": [
            {
                "key": "heart_rate",
                "label": "Heart rate",
                "unit": "bpm",
                "min": 30,
                "max": 220,
                "step": 1,
            }
        ],
    },
}
MEDICATION = "medication"
MEDICATION_LABEL = "Took my medicines today"
BADGES = (7, 30, 100, 365)
READING_DAYS = 14
CALENDAR_DAYS = 35
BOARD_SIZE = 20

ALIAS_FIRST = (
    "Care",
    "Pulse",
    "Vital",
    "Steady",
    "Bright",
    "Calm",
    "Kind",
    "Brave",
    "Gentle",
    "Clear",
    "Healthy",
    "Mindful",
    "Rested",
    "Hopeful",
    "Balanced",
    "Resilient",
)
ALIAS_SECOND = (
    "Compass",
    "Pioneer",
    "Harbor",
    "Lantern",
    "Beacon",
    "Sprout",
    "Summit",
    "River",
    "Meadow",
    "Anchor",
    "Trail",
    "Horizon",
    "Willow",
    "Keeper",
    "Rhythm",
    "Garden",
)


def catalogue() -> list[dict]:
    return [{"key": k, **v} for k, v in MEASURES.items()]


def _bad(code: str, message: str, status: int = 422) -> AuthError:
    return AuthError(status, code, message)


# --- the plan ------------------------------------------------------------------------------


def get_plan(db: Session, patient_id: str) -> CarePlan | None:
    return db.get(CarePlan, patient_id)


def plan_items(db: Session, plan: CarePlan) -> list[str]:
    measures = db.scalars(
        select(CarePlanMeasure.measure)
        .where(CarePlanMeasure.patient_id == plan.patient_id)
        .order_by(CarePlanMeasure.id)
    ).all()
    return list(measures) + ([MEDICATION] if plan.medication_check else [])


def plan_out(db: Session, patient_id: str) -> dict | None:
    plan = get_plan(db, patient_id)
    if plan is None:
        return None
    rows = db.scalars(
        select(CarePlanMeasure)
        .where(CarePlanMeasure.patient_id == patient_id)
        .order_by(CarePlanMeasure.id)
    ).all()
    editor = db.get(User, plan.updated_by_user_id) if plan.updated_by_user_id else None
    return {
        "checkin_quota": plan.checkin_quota,
        "medication_check": plan.medication_check,
        "measures": [
            {"key": m.measure, **MEASURES[m.measure], "targets": m.targets or {}}
            for m in rows
            if m.measure in MEASURES
        ],
        "items": plan_items(db, plan),
        "updated_at": plan.updated_at,
        "updated_by": editor.display_name if editor else None,
    }


def _clean_targets(measure: str, raw: dict | None) -> dict | None:
    """Targets per component: {"systolic": {"low": 90, "high": 130}}; either bound may be
    missing; nothing outside the recordable range; low below high."""
    if not raw:
        return None
    components = {c["key"]: c for c in MEASURES[measure]["components"]}
    out: dict = {}
    for key, bounds in raw.items():
        if key not in components or not isinstance(bounds, dict):
            raise _bad(
                "invalid_target", f"Unknown part of {MEASURES[measure]['label'].lower()}: {key}."
            )
        c = components[key]
        low, high = bounds.get("low"), bounds.get("high")
        for v in (low, high):
            if v is not None and (not isinstance(v, (int, float)) or not c["min"] <= v <= c["max"]):
                raise _bad(
                    "invalid_target",
                    f"{c['label']} target must be between {c['min']} and {c['max']} {c['unit']}.",
                )
        if low is not None and high is not None and low >= high:
            raise _bad(
                "invalid_target", f"{c['label']}: the lower target must be below the upper one."
            )
        if low is not None or high is not None:
            out[key] = {"low": low, "high": high}
    return out or None


def set_plan(db: Session, cm: User, patient: Patient, body: dict) -> dict:
    """Create or replace the plan. At least one item; the quota between 1 and the number of
    items, so it can always be met by doing what the plan asks and never by repetition."""
    measures = body.get("measures") or []
    keys = [m.get("key") for m in measures]
    if any(k not in MEASURES for k in keys):
        raise _bad("unknown_measure", "Choose measurements from the list.")
    if len(set(keys)) != len(keys):
        raise _bad("duplicate_measure", "Each measurement can be tracked once.")
    medication = bool(body.get("medication_check"))
    items = len(keys) + (1 if medication else 0)
    if items == 0:
        raise _bad("empty_plan", "Track at least one measurement or the daily medicines check.")
    quota = body.get("checkin_quota")
    if not isinstance(quota, int) or not 1 <= quota <= items:
        raise _bad("invalid_quota", f"The daily check-in quota must be between 1 and {items}.")
    targets = {m["key"]: _clean_targets(m["key"], m.get("targets")) for m in measures}

    plan = db.get(CarePlan, patient.patient_id)
    created = plan is None
    if plan is None:
        plan = CarePlan(patient_id=patient.patient_id)
        db.add(plan)
    plan.checkin_quota, plan.medication_check, plan.updated_by_user_id = quota, medication, cm.id
    plan.updated_at = utcnow()
    existing = {
        m.measure: m
        for m in db.scalars(
            select(CarePlanMeasure).where(CarePlanMeasure.patient_id == patient.patient_id)
        )
    }
    for key, row in existing.items():
        if key not in targets:
            db.delete(row)
    for key in keys:
        row = existing.get(key)
        if row is None:
            db.add(
                CarePlanMeasure(patient_id=patient.patient_id, measure=key, targets=targets[key])
            )
        else:
            row.targets = targets[key]
    db.flush()
    audit.record(
        db,
        "care_plan_created" if created else "care_plan_updated",
        "patient",
        patient.patient_id,
        actor=cm.username,
        actor_role=cm.role,
        detail={"measures": keys, "medication_check": medication, "checkin_quota": quota},
    )
    return plan_out(db, patient.patient_id)


# --- what the patient records -------------------------------------------------------------


def _require_plan(db: Session, patient_id: str) -> CarePlan:
    plan = get_plan(db, patient_id)
    if plan is None:
        raise _bad("no_plan", "Your care manager has not set up a plan yet.", 409)
    return plan


def record_reading(
    db: Session, user: User, patient_id: str, measure: str, values: dict, taken_at: datetime | None
) -> dict:
    plan = _require_plan(db, patient_id)
    if measure not in plan_items(db, plan) or measure == MEDICATION:
        raise _bad("not_in_plan", "This measurement is not part of your plan.")
    clean = {}
    for c in MEASURES[measure]["components"]:
        v = values.get(c["key"])
        if not isinstance(v, (int, float)) or not c["min"] <= v <= c["max"]:
            raise _bad(
                "invalid_reading",
                f"{c['label']} must be between {c['min']} and {c['max']} {c['unit']}.",
            )
        clean[c["key"]] = round(float(v), 1)
    now = utcnow()
    when = taken_at or now
    if when > now + timedelta(minutes=5):
        raise _bad("future_reading", "A reading cannot be in the future.")
    if when < now - timedelta(days=READING_DAYS):
        raise _bad("old_reading", f"Readings older than {READING_DAYS} days cannot be added.")
    reading = HealthReading(patient_id=patient_id, measure=measure, values=clean, taken_at=when)
    db.add(reading)
    db.flush()
    audit.record(
        db,
        "reading_recorded",
        "patient",
        patient_id,
        actor=user.username,
        actor_role=user.role,
        detail={"measure": measure},
    )
    # A check-in is completing the plan item today (whenever the reading was taken).
    _checkin(db, user, patient_id, plan, measure, reading.id)
    return {"id": reading.id, "measure": measure, "values": clean, "taken_at": when}


def confirm_medication(db: Session, user: User, patient_id: str) -> None:
    plan = _require_plan(db, patient_id)
    if not plan.medication_check:
        raise _bad("not_in_plan", "The medicines check is not part of your plan.")
    _checkin(db, user, patient_id, plan, MEDICATION, None)


def _checkin(
    db: Session, user: User, patient_id: str, plan: CarePlan, item: str, reading_id: int | None
) -> None:
    today = clock.get_today(db)
    exists = db.scalar(
        select(CheckIn.id).where(
            CheckIn.patient_id == patient_id, CheckIn.day == today, CheckIn.item == item
        )
    )
    if exists is None:
        db.add(CheckIn(patient_id=patient_id, day=today, item=item, reading_id=reading_id))
        db.flush()
    _maybe_award(db, user, patient_id, plan, today)


def _done_today(db: Session, patient_id: str, plan: CarePlan, day: date) -> list[str]:
    items = set(plan_items(db, plan))
    done = db.scalars(
        select(CheckIn.item).where(CheckIn.patient_id == patient_id, CheckIn.day == day)
    ).all()
    return [i for i in done if i in items]


def _maybe_award(db: Session, user: User, patient_id: str, plan: CarePlan, day: date) -> None:
    """One coin when today's distinct plan items reach the quota. The unique (patient, day)
    row makes a second award impossible even under concurrent requests."""
    if len(_done_today(db, patient_id, plan, day)) < plan.checkin_quota:
        return
    if db.scalar(
        select(CoinLedger.id).where(CoinLedger.patient_id == patient_id, CoinLedger.day == day)
    ):
        return
    try:
        with db.begin_nested():
            db.add(CoinLedger(patient_id=patient_id, day=day, amount=1, reason="daily_quota"))
    except IntegrityError:
        return
    audit.record(
        db,
        "coin_awarded",
        "patient",
        patient_id,
        actor=user.username,
        actor_role=user.role,
        detail={"day": day.isoformat()},
    )


# --- summaries ----------------------------------------------------------------------------


def today_progress(db: Session, patient_id: str) -> dict | None:
    plan = get_plan(db, patient_id)
    if plan is None:
        return None
    today = clock.get_today(db)
    done = _done_today(db, patient_id, plan, today)
    return {
        "day": today,
        "done": done,
        "quota": plan.checkin_quota,
        "met": len(done) >= plan.checkin_quota,
        "coin_today": bool(
            db.scalar(
                select(CoinLedger.id).where(
                    CoinLedger.patient_id == patient_id, CoinLedger.day == today
                )
            )
        ),
    }


def checkin_calendar(
    db: Session, patient_id: str, today: date, days: int = CALENDAR_DAYS
) -> dict | None:
    """Completed plan items per day for the last `days` days, with the days a coin was
    earned. Days before the plan existed are marked, not counted as missed."""
    plan = get_plan(db, patient_id)
    if plan is None:
        return None
    first = today - timedelta(days=days - 1)
    counts = dict(
        db.execute(
            select(CheckIn.day, func.count(func.distinct(CheckIn.item)))
            .where(CheckIn.patient_id == patient_id, CheckIn.day >= first)
            .group_by(CheckIn.day)
        ).all()
    )
    coins = set(
        db.scalars(
            select(CoinLedger.day).where(
                CoinLedger.patient_id == patient_id, CoinLedger.day >= first
            )
        )
    )
    start = plan.created_at.date()
    cells = []
    for i in range(days):
        d = first + timedelta(days=i)
        cells.append(
            {"date": d, "count": counts.get(d, 0), "coin": d in coins, "before_plan": d < start}
        )
    return {"quota": plan.checkin_quota, "items": len(plan_items(db, plan)), "days": cells}


def readings_series(
    db: Session, patient_id: str, now: datetime, days: int = READING_DAYS
) -> list[dict]:
    """For each tracked measurement: its readings over the last `days` days, units and any
    targets the care manager set. Nothing is filled in where there is no reading."""
    plan = plan_out(db, patient_id)
    if plan is None:
        return []
    since = now - timedelta(days=days)
    out = []
    for m in plan["measures"]:
        rows = db.scalars(
            select(HealthReading)
            .where(
                HealthReading.patient_id == patient_id,
                HealthReading.measure == m["key"],
                HealthReading.taken_at >= since,
            )
            .order_by(HealthReading.taken_at)
        ).all()
        out.append(
            {
                "key": m["key"],
                "label": m["label"],
                "components": m["components"],
                "targets": m["targets"],
                "readings": [
                    {"id": r.id, "taken_at": r.taken_at, "values": r.values} for r in rows
                ],
            }
        )
    return out


def coins(db: Session, patient_id: str) -> dict:
    rows = db.scalars(
        select(CoinLedger)
        .where(CoinLedger.patient_id == patient_id)
        .order_by(CoinLedger.day.desc())
    ).all()
    balance = sum(r.amount for r in rows)
    return {
        "balance": balance,
        "history": [{"day": r.day, "amount": r.amount, "reason": r.reason} for r in rows[:30]],
        "badges": [{"at": n, "earned": balance >= n} for n in BADGES],
        "next_badge": next((n for n in BADGES if balance < n), None),
    }


# --- the opt-in board ---------------------------------------------------------------------


def _new_alias(db: Session) -> str:
    taken = set(db.scalars(select(LeaderboardProfile.alias)))
    rng = random.SystemRandom()
    for _ in range(200):
        alias = f"{rng.choice(ALIAS_FIRST)} {rng.choice(ALIAS_SECOND)}"
        if alias not in taken:
            return alias
    return f"{rng.choice(ALIAS_FIRST)} {rng.choice(ALIAS_SECOND)} {rng.randint(10, 99)}"


def set_board(db: Session, user: User, patient_id: str, opted_in: bool) -> dict:
    profile = db.get(LeaderboardProfile, patient_id)
    if profile is None:
        profile = LeaderboardProfile(patient_id=patient_id, alias=_new_alias(db), opted_in=opted_in)
        db.add(profile)
    profile.opted_in = opted_in
    db.flush()
    audit.record(
        db,
        "board_opt_in" if opted_in else "board_opt_out",
        "patient",
        patient_id,
        actor=user.username,
        actor_role=user.role,
    )
    return board(db, patient_id)


def board(db: Session, patient_id: str) -> dict:
    """This month's coins for every opted-in patient, by alias and country only."""
    me = db.get(LeaderboardProfile, patient_id)
    today = clock.get_today(db)
    first = today.replace(day=1)
    rows = db.execute(
        select(
            LeaderboardProfile.patient_id,
            LeaderboardProfile.alias,
            Patient.country,
            func.coalesce(func.sum(CoinLedger.amount), 0),
        )
        .join(Patient, Patient.patient_id == LeaderboardProfile.patient_id)
        .outerjoin(
            CoinLedger,
            (CoinLedger.patient_id == LeaderboardProfile.patient_id) & (CoinLedger.day >= first),
        )
        .where(LeaderboardProfile.opted_in.is_(True))
        .group_by(LeaderboardProfile.patient_id, LeaderboardProfile.alias, Patient.country)
    ).all()
    ranked = sorted(rows, key=lambda r: (-r[3], r[1]))
    entries = [
        {
            "rank": i + 1,
            "alias": alias,
            "country": country,
            "coins": int(n),
            "you": pid == patient_id,
        }
        for i, (pid, alias, country, n) in enumerate(ranked)
    ]
    mine = next((e for e in entries if e["you"]), None)
    return {
        "opted_in": bool(me and me.opted_in),
        "alias": me.alias if me else None,
        "month": first,
        "entries": entries[:BOARD_SIZE],
        "you": mine,
        "participants": len(entries),
    }
