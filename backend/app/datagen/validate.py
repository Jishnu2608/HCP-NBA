"""Data-quality checks run after every seed: integrity, chronology, history gates, distributions."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.datagen.content import valid_on
from app.datagen.generate import consent_checker
from app.datagen.heroes import HCP_HEROES, PATIENT_HEROES
from app.models import (
    CareManagerPatient,
    Consent,
    Content,
    Hcp,
    Interaction,
    MedicationFill,
    Patient,
    PatientHcp,
    PatientTherapy,
    RepHcp,
    SimLatent,
)
from app.models.enums import MlrStatus, Outcome, TargetType


@dataclass
class Check:
    name: str
    passed: bool
    detail: str


def validate(db: Session) -> list[Check]:
    as_of = clock.get_today(db)
    end_of_day = datetime.combine(as_of, time.max)
    checks: list[Check] = []

    def check(name: str, bad: int | bool, detail: str = "") -> None:
        failed = bad if isinstance(bad, bool) else bad > 0
        suffix = f"{bad} violations" if not isinstance(bad, bool) and bad else ""
        checks.append(Check(name, not failed, " ".join(x for x in (detail, suffix) if x)))

    hcps = {h.hcp_id: h for h in db.scalars(select(Hcp))}
    patients = {p.patient_id: p for p in db.scalars(select(Patient))}
    therapies = list(db.scalars(select(PatientTherapy)))
    fills = list(db.scalars(select(MedicationFill)))
    interactions = list(db.scalars(select(Interaction)))
    contents = {c.content_id: c for c in db.scalars(select(Content))}
    consents = defaultdict(list)
    for c in db.scalars(select(Consent)):
        consents[c.patient_id].append(c)

    # Referential integrity, including the polymorphic target reference.
    targets = {TargetType.HCP: hcps, TargetType.PATIENT: patients}
    check(
        "interaction targets exist",
        sum(1 for i in interactions if i.target_id not in targets[i.target_type]),
    )
    therapy_by_id = {t.id: t for t in therapies}
    check(
        "fills belong to their patient's therapy",
        sum(
            1
            for f in fills
            if f.therapy_id not in therapy_by_id
            or therapy_by_id[f.therapy_id].patient_id != f.patient_id
        ),
    )
    with_therapy = {t.patient_id for t in therapies}
    check("every patient has a therapy", len(patients.keys() - with_therapy))
    primaries = Counter(
        ph.patient_id for ph in db.scalars(select(PatientHcp).where(PatientHcp.is_primary))
    )
    check(
        "every patient has exactly one primary HCP",
        sum(1 for p in patients if primaries[p] != 1),
    )
    managed = set(db.scalars(select(CareManagerPatient.patient_id)))
    check("every patient has a care manager", len(patients.keys() - managed))
    covered = set(db.scalars(select(RepHcp.hcp_id)))
    check("every HCP has a rep", len(hcps.keys() - covered))
    latent = {(s.target_type, s.target_id) for s in db.scalars(select(SimLatent))}
    check(
        "every target has latent traits",
        sum(1 for t, ids in targets.items() for i in ids if (t, i) not in latent),
    )

    # Chronology.
    fills_by_therapy = defaultdict(list)
    for f in fills:
        fills_by_therapy[f.therapy_id].append(f)
    check(
        "fills fall between therapy start and as-of date",
        sum(1 for f in fills if not therapy_by_id[f.therapy_id].start_date <= f.fill_date <= as_of),
    )
    refill_too_soon = 0
    for rows in fills_by_therapy.values():
        rows.sort(key=lambda f: f.fill_date)
        for prev, nxt in zip(rows, rows[1:], strict=False):
            if (nxt.fill_date - prev.fill_date).days < prev.days_supply - 7:
                refill_too_soon += 1
    check("no refill more than 7 days early", refill_too_soon)
    check(
        "interactions are not in the future",
        sum(1 for i in interactions if i.int_ts > end_of_day),
    )
    check(
        "outcomes follow their interaction",
        sum(1 for i in interactions if i.outcome_ts and not i.int_ts <= i.outcome_ts <= end_of_day),
    )
    check(
        "patients are adults born before their first therapy",
        sum(1 for p in patients.values() if (as_of - p.birth_date).days < 18 * 365),
    )

    # History must already respect the hard gates.
    checkers = {pid: consent_checker(rows) for pid, rows in consents.items()}
    check(
        "no patient outreach on a non-consented channel",
        sum(
            1
            for i in interactions
            if i.target_type == TargetType.PATIENT
            and not checkers[i.target_id](i.channel, i.int_ts.date())
        ),
    )
    check(
        "history only used content that was MLR-valid at the time",
        sum(
            1
            for i in interactions
            if i.content_id and not valid_on(contents[i.content_id], i.int_ts.date())
        ),
    )
    check(
        "content channel matches interaction channel",
        sum(
            1
            for i in interactions
            if i.content_id and i.channel not in contents[i.content_id].channels
        ),
    )

    # Distributions and demo coverage.
    statuses = Counter(c.mlr_status for c in contents.values())
    expired = sum(1 for c in contents.values() if c.expiry_date and c.expiry_date <= as_of)
    check(
        "content covers approved, pending, rejected and expired",
        # The built-in library starts with these states; later ones come from MLR decisions.
        not (
            all(statuses[s] for s in (MlrStatus.APPROVED, MlrStatus.PENDING, MlrStatus.REJECTED))
            and expired
        ),
        f"{dict(statuses)}, expired={expired}",
    )
    any_consent = sum(1 for pid in patients if any(c.granted and c.channel for c in consents[pid]))
    share = any_consent / len(patients)
    check("85-99% of patients reachable on some channel", not 0.85 <= share <= 0.99, f"{share:.1%}")

    patient_touches = [i for i in interactions if i.target_type == TargetType.PATIENT]
    hcp_touches = [i for i in interactions if i.target_type == TargetType.HCP]
    quiet = (Outcome.NO_RESPONSE, Outcome.DECLINED)
    p_rate = sum(1 for i in patient_touches if i.outcome not in quiet) / len(patient_touches)
    h_rate = sum(1 for i in hcp_touches if i.outcome not in quiet) / len(hcp_touches)
    check(
        "patient response rate is plausible (15-50%)", not 0.15 <= p_rate <= 0.50, f"{p_rate:.1%}"
    )
    check("HCP response rate is plausible (8-40%)", not 0.08 <= h_rate <= 0.40, f"{h_rate:.1%}")

    last_runout = {}
    for tid, rows in fills_by_therapy.items():
        last = rows[-1]
        last_runout[tid] = (as_of - last.fill_date).days - last.days_supply
    in_gap = sum(1 for t in therapies if last_runout.get(t.id, 1) > 0) / len(therapies)
    check("20-60% of therapies currently in a gap", not 0.20 <= in_gap <= 0.60, f"{in_gap:.1%}")
    never_filled = sum(1 for t in therapies if t.id not in fills_by_therapy) / len(therapies)
    check(
        "1-10% of therapies never filled (primary non-adherence)",
        not 0.01 <= never_filled <= 0.10,
        f"{never_filled:.1%}",
    )

    hero_ids = [f"HCP_{i:04d}" for i in HCP_HEROES] + [f"PAT_{i:05d}" for i in PATIENT_HEROES]
    check(
        "hero records present",
        sum(1 for h in hero_ids if h not in hcps and h not in patients),
    )
    return checks
