"""De-identified panel adherence: where patient adherence meets HCP engagement.

The field guide's "one engine, both audiences": an HCP's engagement plan can take into account
how the patients they care for are getting on with their medicines, and a patient's outreach
can note that their prescriber recently engaged with related education. That must never turn
into patient-level information reaching the commercial side, so every figure here is:

- **Consented.** Only patients with a granted, in-effect `aggregate_insights` consent are
  counted. Sharing with one's own doctors (`provider_sharing`) and outreach consent do not
  imply it. Real patients are counted only after they switch it on themselves.
- **Confirmed.** Only medications the care team confirmed, with a known measure (the same
  population the engine scores).
- **Grouped.** A figure is shown or used only for a group of at least `aggregate_min_cohort`
  distinct patients (default 11, the usual small-cell suppression rule). An HCP's own
  attributed panel is used only when it and the rest of its area are both that large, so the
  two figures can never be subtracted to describe a smaller group. Otherwise the HCP's US state
  is the group, and failing that the figure is suppressed.
- **Anonymous.** Results carry counts and shares, rounded to whole percentages, never a
  patient id, name, medicine name or date.

The NBA engine uses the same figures to favour adherence-support material for an HCP whose
group is below target in a measure (`nba/engine.py`); the HCP 360 page shows them. Gates,
MLR and consent checks are unchanged by any of this.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.features.adherence import compute_adherence
from app.models import Consent, Hcp, MedicationFill, Patient, PatientHcp, PatientTherapy
from app.models.enums import ConsentPurpose, Measure, ReviewStatus

MEASURE_LABEL = {
    Measure.DIABETES: "Diabetes medicines",
    Measure.HYPERTENSION: "Blood pressure medicines (RAS antagonists)",
    Measure.CHOLESTEROL: "Cholesterol medicines (statins)",
}
CONSENT_BASIS = (
    "Patients who opted in to de-identified aggregate statistics, on medicines their care team "
    "confirmed. Groups smaller than the minimum are never shown."
)


@dataclass(frozen=True)
class Point:
    """One confirmed therapy of one consenting patient, as of the day. Never leaves this
    module: callers receive only the group summaries."""

    patient_id: str
    measure: str
    state: str | None
    pdc: float
    in_gap: bool


def aggregate_consent(consents: Iterable[Consent], day: date) -> bool:
    """True only if a granted aggregate-statistics consent is in effect on the day."""
    return any(
        c.granted
        and c.purpose == ConsentPurpose.AGGREGATE_INSIGHTS
        and c.effective_from <= day
        and (c.effective_to is None or day < c.effective_to)
        for c in consents
    )


def points(
    patients: dict[str, Patient],
    therapies: dict[str, list[PatientTherapy]],
    fills: dict[int, list[tuple[date, int]]],
    consents: dict[str, list[Consent]],
    day: date,
    window_days: int,
) -> list[Point]:
    out = []
    for pid, patient in patients.items():
        if not aggregate_consent(consents.get(pid, []), day):
            continue
        for t in therapies.get(pid, []):
            if t.review_status != ReviewStatus.CONFIRMED or not t.measure:
                continue
            a = compute_adherence(fills.get(t.id, []), t.start_date, day, window_days)
            out.append(Point(pid, str(t.measure), patient.state, a.pdc, a.gap_days > 0))
    return out


def points_from_population(pop, settings: dict) -> list[Point]:
    return points(
        pop.patients, pop.therapies, pop.fills, pop.consents, pop.as_of, settings["pdc_window_days"]
    )


def points_from_db(db: Session, day: date, settings: dict) -> list[Point]:
    """The same points read directly (for one page view, without loading the whole engine
    population). Only consenting patients' rows are read."""
    consents: dict[str, list[Consent]] = defaultdict(list)
    for c in db.scalars(
        select(Consent).where(Consent.purpose == ConsentPurpose.AGGREGATE_INSIGHTS)
    ):
        consents[c.patient_id].append(c)
    ids = {pid for pid, rows in consents.items() if aggregate_consent(rows, day)}
    if not ids:
        return []
    patients = {
        p.patient_id: p for p in db.scalars(select(Patient).where(Patient.patient_id.in_(ids)))
    }
    therapies: dict[str, list[PatientTherapy]] = defaultdict(list)
    for t in db.scalars(
        select(PatientTherapy).where(
            PatientTherapy.patient_id.in_(ids),
            PatientTherapy.review_status == ReviewStatus.CONFIRMED,
            PatientTherapy.measure.is_not(None),
        )
    ):
        therapies[t.patient_id].append(t)
    fills: dict[int, list[tuple[date, int]]] = defaultdict(list)
    for f in db.scalars(
        select(MedicationFill)
        .where(MedicationFill.patient_id.in_(ids))
        .order_by(MedicationFill.fill_date)
    ):
        fills[f.therapy_id].append((f.fill_date, f.days_supply))
    return points(patients, therapies, fills, consents, day, settings["pdc_window_days"])


def attributed_ids(db: Session, hcp_id: str) -> set[str]:
    return set(db.scalars(select(PatientHcp.patient_id).where(PatientHcp.hcp_id == hcp_id)))


def summary(group: list[Point], threshold: float) -> dict:
    n = len({p.patient_id for p in group})
    return {
        "patients": n,
        "adherent_share": round(sum(p.pdc >= threshold for p in group) / len(group), 2),
        "mean_pdc": round(sum(p.pdc for p in group) / len(group), 2),
        "in_gap_share": round(sum(p.in_gap for p in group) / len(group), 2),
    }


def _patients(group: list[Point]) -> int:
    return len({p.patient_id for p in group})


def panel(hcp: Hcp, attributed: set[str], pts: list[Point], settings: dict) -> dict:
    """Per measure: the HCP's group, its figures if large enough, and whether it is below the
    adherence target."""
    k = settings["aggregate_min_cohort"]
    threshold = settings["pdc_threshold"]
    target = settings["panel_adherence_target"]
    # Area groups only for a US HCP with a state (synthetic patients are US residents).
    area_ok = bool(hcp.state) and hcp.country in (None, "US")
    rows = []
    for measure in Measure:
        in_measure = [p for p in pts if p.measure == measure]
        own = [p for p in in_measure if p.patient_id in attributed]
        area = [p for p in in_measure if area_ok and p.state == hcp.state]
        rest = [p for p in area if p.patient_id not in attributed]
        if _patients(own) >= k and _patients(rest) >= k:
            level, group = "attributed", own
            scope = "patients attributed to this HCP"
        elif _patients(area) >= k:
            level, group = "area", area
            scope = f"patients in {hcp.state}"
        else:
            level, group, scope = None, [], None
        row = {
            "measure": str(measure),
            "label": MEASURE_LABEL[measure],
            "level": level,
            "scope": scope,
            "suppressed": level is None,
        }
        if level:
            row.update(summary(group, threshold))
            row["below_target"] = row["adherent_share"] < target
        rows.append(row)
    return {
        "min_cohort": k,
        "pdc_threshold": threshold,
        "target": target,
        "basis": CONSENT_BASIS,
        "measures": rows,
    }


def below_target(panel_out: dict, measure: str | None) -> dict | None:
    """The group figure behind an adherence-support signal for this measure, if any."""
    if measure is None:
        return None
    return next(
        (r for r in panel_out["measures"] if r["measure"] == measure and r.get("below_target")),
        None,
    )


def _lower_first(text: str) -> str:
    """ "Blood pressure medicines (RAS antagonists)" -> "blood pressure medicines (RAS ...)"."""
    return text[:1].lower() + text[1:]


def reason_text(row: dict, panel_out: dict) -> str:
    return (
        f"De-identified group: {row['adherent_share']:.0%} of {row['patients']} consenting "
        f"{row['scope']} on {_lower_first(row['label'])} are at PDC "
        f"{panel_out['pdc_threshold']:.0%} or more (target {panel_out['target']:.0%}), so "
        "adherence-support material for this measure is favoured"
    )
