"""STAR-style medication adherence measures and persistence, for the population-health view.

These are measures *calculated* on this system's data, in the style of the Medicare Part D
adherence measures (diabetes medicines, RAS antagonists, statins). They are not Star Ratings:
a rating needs the official specification, the full plan population, risk adjustment, CMS cut
points and an audited submission, none of which exist here. The screen says so.

Definitions (every number on the screen comes from these):

- **Measurement period**: the rolling `pdc_window_days` (180) ending on the as-of day. CMS uses
  the calendar year; a rolling window keeps the demo current at any date.
- **Denominator**: a confirmed therapy in one of the three measures with at least two fills on
  different days on or before the as-of day, the first at least 91 days before it, and at
  least one fill inside the period (the CMS-style "two fills, enough time to judge" rule).
- **Numerator**: PDC at or above `pdc_threshold` (80%). PDC runs from the first fill in the
  period to the as-of day; early refills are carried forward, not double counted.
- **Not modelled**: hospice, ESRD and other CMS exclusions, insulin exclusion for the diabetes
  measure, and mid-year enrolment. They are listed on the screen.
- **Persistence**: from the first fill, the course continues until the supply runs out and no
  fill follows within `persistence_gap_days` (60). Reported as the share of courses still
  going 180 and 365 days after they started, among courses that started at least that long
  ago (so recent starters are not counted as persistent by default).

Groups smaller than `aggregate_min_cohort` are suppressed. The caller passes the set of
patients in the reader's scope; individual figures for one patient are on Patient 360.
"""

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.adherence import compute_adherence, compute_persistence
from app.models import Interaction, MedicationFill, Patient, PatientTherapy
from app.models.enums import Measure, PatientOrigin, ReviewStatus, TargetType
from app.panel_adherence import MEASURE_LABEL

MIN_FIRST_FILL_DAYS = 91
TREND_POINTS = 6
PERSISTENCE_HORIZONS = (180, 365)
NOT_MODELLED = [
    "Hospice, ESRD and other CMS denominator exclusions",
    "Insulin users excluded from the diabetes measure",
    "Enrolment periods and plan switching (every patient is treated as enrolled throughout)",
    "Official cut points, case-mix adjustment and audited submission (no Star Rating is implied)",
]


def _month_ends(today: date, n: int) -> list[date]:
    out, cursor = [], today.replace(day=1) - timedelta(days=1)
    while len(out) < n - 1:
        out.append(cursor)
        cursor = cursor.replace(day=1) - timedelta(days=1)
    return sorted(out) + [today]


def _in_denominator(fills: list[tuple[date, int]], as_of: date, window: int) -> bool:
    known = sorted(d for d, _ in fills if d <= as_of)
    if len(set(known)) < 2:
        return False
    if (as_of - known[0]).days < MIN_FIRST_FILL_DAYS:
        return False
    return known[-1] >= as_of - timedelta(days=window - 1)


def _load(db: Session, patient_ids: set[str] | None):
    query = select(PatientTherapy).where(
        PatientTherapy.review_status == ReviewStatus.CONFIRMED,
        PatientTherapy.measure.is_not(None),
    )
    if patient_ids is not None:
        query = query.where(PatientTherapy.patient_id.in_(patient_ids))
    therapies = db.scalars(query).all()
    ids = {t.id for t in therapies}
    fills: dict[int, list[tuple[date, int]]] = defaultdict(list)
    if ids:
        for therapy_id, day, supply in db.execute(
            select(MedicationFill.therapy_id, MedicationFill.fill_date, MedicationFill.days_supply)
            .where(MedicationFill.therapy_id.in_(ids))
            .order_by(MedicationFill.fill_date)
        ):
            fills[therapy_id].append((day, supply))
    return therapies, fills


def _rate(part: int, whole: int, k: int) -> dict:
    if whole < k:
        return {"n": whole, "rate": None, "suppressed": True}
    return {"n": whole, "rate": round(part / whole, 3), "suppressed": False}


def _split(pdcs: dict[int, float], ids: set[int], threshold: float, k: int) -> dict:
    members = [tid for tid in pdcs if tid in ids]
    return _rate(sum(pdcs[t] >= threshold for t in members), len(members), k)


def measures(db: Session, today: date, settings: dict, patient_ids: set[str] | None) -> dict:
    """Measure rates now and at recent month ends, persistence, and the engine-contacted split.
    `patient_ids` None means the whole population (a reader with an all-patient scope)."""
    window = settings["pdc_window_days"]
    threshold = settings["pdc_threshold"]
    k = settings["aggregate_min_cohort"]
    gap = settings["persistence_gap_days"]
    therapies, fills = _load(db, patient_ids)
    origins = dict(
        db.execute(
            select(Patient.patient_id, Patient.origin).where(
                Patient.patient_id.in_({t.patient_id for t in therapies})
            )
        ).all()
    )
    period_start = today - timedelta(days=window - 1)
    contacted = {
        tid
        for tid in db.scalars(
            select(Interaction.therapy_id).where(
                Interaction.target_type == TargetType.PATIENT,
                Interaction.source == "nba",
                Interaction.therapy_id.in_({t.id for t in therapies}),
                Interaction.int_ts >= period_start,
            )
        )
        if tid
    }

    points = _month_ends(today, TREND_POINTS)
    rows = []
    for measure in Measure:
        group = [t for t in therapies if t.measure == measure]
        trend = []
        for day in points:
            denom = [t for t in group if _in_denominator(fills.get(t.id, []), day, window)]
            adherent = sum(
                compute_adherence(fills.get(t.id, []), t.start_date, day, window).pdc >= threshold
                for t in denom
            )
            trend.append({"as_of": day, **_rate(adherent, len(denom), k)})
        now = [t for t in group if _in_denominator(fills.get(t.id, []), today, window)]
        pdcs = {
            t.id: compute_adherence(fills.get(t.id, []), t.start_date, today, window).pdc
            for t in now
        }

        courses = [compute_persistence(fills.get(t.id, []), today, gap) for t in group]
        courses = [c for c in courses if c.first_fill is not None]
        persistence = []
        for horizon in PERSISTENCE_HORIZONS:
            eligible = [c for c in courses if (today - c.first_fill).days >= horizon]
            kept = sum(c.days_persistent >= horizon for c in eligible)
            persistence.append({"days": horizon, **_rate(kept, len(eligible), k)})
        stopped = sorted(c.days_persistent for c in courses if c.discontinued)
        rows.append(
            {
                "measure": str(measure),
                "label": MEASURE_LABEL[measure],
                "current": trend[-1],
                "trend": trend,
                "contacted": _split(pdcs, contacted, threshold, k),
                "not_contacted": _split(pdcs, set(pdcs) - contacted, threshold, k),
                "persistence": persistence,
                "courses": len(courses),
                "currently_persistent": _rate(
                    sum(not c.discontinued for c in courses), len(courses), k
                ),
                "median_days_to_discontinuation": (
                    stopped[len(stopped) // 2] if len(stopped) >= k else None
                ),
            }
        )
    patients = {t.patient_id for t in therapies}
    return {
        "as_of": today,
        "period_days": window,
        "pdc_threshold": threshold,
        "persistence_gap_days": gap,
        "min_group": k,
        "patients": len(patients),
        "synthetic_patients": sum(origins.get(p) == PatientOrigin.SYNTHETIC for p in patients),
        "measures": rows,
        "not_modelled": NOT_MODELLED,
    }


def therapy_persistence(fills: list[tuple[date, int]], today: date, gap: int) -> dict:
    """One patient's course of one medicine, for Patient 360."""
    p = compute_persistence(fills, today, gap)
    return {
        "first_fill": p.first_fill,
        "days_persistent": p.days_persistent,
        "discontinued": p.discontinued,
        "discontinued_on": p.discontinued_on,
        "gap_days_allowed": gap,
    }
