"""A patient's own figures: supply on hand per medication and where each request stands.

Nothing here shows risk scores or model outputs (they are internal to the care team)."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.adherence import coverage_intervals
from app.insights import streaks
from app.models import CareRequest, MedicationFill, PatientTherapy
from app.models.enums import CareRequestStatus, CareRequestType, ReviewStatus

WINDOW_DAYS = 30


def medicine_on_hand(db: Session, patient_id: str, today: date, days: int = WINDOW_DAYS) -> dict:
    """For each confirmed, active medication: one cell per day of the last `days` days,
    "covered" (a recorded refill covered the day), "gap" (it did not) or "before" (the
    medication had not started). Plus the patient's medicine-on-hand streak."""
    therapies = db.scalars(
        select(PatientTherapy)
        .where(
            PatientTherapy.patient_id == patient_id,
            PatientTherapy.review_status == ReviewStatus.CONFIRMED,
            PatientTherapy.status == "active",
        )
        .order_by(PatientTherapy.drug_name)
    ).all()
    first = today - timedelta(days=days - 1)
    medicines, coverage = [], []
    for t in therapies:
        fills = db.execute(
            select(MedicationFill.fill_date, MedicationFill.days_supply).where(
                MedicationFill.therapy_id == t.id, MedicationFill.fill_date <= today
            )
        ).all()
        intervals = coverage_intervals((d, s) for d, s in fills)
        coverage.append((t.start_date, intervals))
        cells = []
        for i in range(days):
            day = first + timedelta(days=i)
            if day < t.start_date:
                state = "before"
            elif any(a <= day < b for a, b in intervals):
                state = "covered"
            else:
                state = "gap"
            cells.append({"date": day, "state": state})
        counted = [c for c in cells if c["state"] != "before"]
        covered_until = max((b for _, b in intervals), default=None)
        medicines.append(
            {
                "therapy_id": t.id,
                "drug_name": t.drug_name,
                "days": cells,
                "covered_days": sum(1 for c in counted if c["state"] == "covered"),
                "counted_days": len(counted),
                "last_refill": max((d for d, _ in fills), default=None),
                # The first day with no supply left, if the latest refill runs out soon.
                "supply_until": covered_until - timedelta(days=1) if covered_until else None,
            }
        )
    return {
        "window_days": days,
        "from": first,
        "to": today,
        "medicines": medicines,
        "streak": streaks.medicine_on_hand(coverage, today),
    }


# The steps of a consultation as the patient, the care manager and the HCP see them.
CONSULTATION_STEPS = (
    ("requested", "Requested"),
    ("care_manager", "With your care manager"),
    ("hcp", "With the healthcare professional"),
    ("answered", "Answered"),
    ("closed", "Closed"),
)


def request_progress(r: CareRequest) -> dict | None:
    """Where a consultation stands, step by step, with the date each step happened.

    Only consultations have this path. A step is "done" when the record shows it happened,
    "current" for the step the request is in now, "todo" for steps still ahead and
    "skipped" for steps a closed request never went through (withdrawn before an answer)."""
    if r.type != CareRequestType.CONSULTATION:
        return None
    status = r.status
    answered = r.responded_at is not None and status in (
        CareRequestStatus.HCP_RESPONDED,
        CareRequestStatus.CLOSED,
    )
    if status == CareRequestStatus.CLOSED:
        current = None
    elif status == CareRequestStatus.HCP_RESPONDED:
        current = "answered"
    elif status == CareRequestStatus.AWAITING_HCP:
        current = "hcp"
    else:
        current = "care_manager"
    dates = {
        "requested": r.created_at,
        "hcp": r.routed_at
        if answered or status in (CareRequestStatus.AWAITING_HCP, CareRequestStatus.CLOSED)
        else None,
        "answered": r.responded_at if answered else None,
        "closed": r.closed_at if status == CareRequestStatus.CLOSED else None,
    }
    order = [key for key, _ in CONSULTATION_STEPS]
    reached = order.index(current) if current else len(order)
    steps = []
    for i, (key, label) in enumerate(CONSULTATION_STEPS):
        if current is None:
            missed = (key == "hcp" and r.routed_at is None) or (key == "answered" and not answered)
            state = "skipped" if missed else "done"
        elif i < reached:
            state = "done"
        elif i == reached:
            state = "current"
        else:
            state = "todo"
        steps.append({"key": key, "label": label, "state": state, "date": dates.get(key)})
    return {"current": current, "steps": steps}
