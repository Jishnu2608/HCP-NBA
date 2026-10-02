"""Leadership metrics: what the engine recommended, what was blocked, and what changed."""

from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.engine_config import get_config
from app.features.engagement import QUIET, patient_training_rows
from app.features.population import load_population
from app.models import (
    AdherenceSnapshot,
    EngineCycle,
    Hcp,
    Interaction,
    Nba,
    NbaCandidate,
    Patient,
    PatientTherapy,
)
from app.models.enums import NbaStatus, Outcome, TargetType
from app.nba import gates
from app.nba.rationale import ACTION_LABEL, CHANNEL_LABEL


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def recommendation_summary(db: Session) -> dict:
    latest = db.scalar(select(func.max(EngineCycle.id)))
    by_status = Counter()
    by_mix = Counter()
    for nba in db.scalars(select(Nba).where(Nba.cycle_id == latest)):
        by_status[(nba.target_type, nba.status)] += 1
        if nba.status != NbaStatus.BLOCKED:
            by_mix[(nba.target_type, nba.action, nba.channel)] += 1
    lifetime = dict(db.execute(select(Nba.status, func.count()).group_by(Nba.status)).all())

    # Why options were held back, counted over every candidate the engine considered.
    reasons = Counter()
    for failures in db.scalars(
        select(NbaCandidate.gate_failures).where(
            NbaCandidate.cycle_id == latest, NbaCandidate.eligible.is_(False)
        )
    ):
        for code in failures:
            reasons[code] += 1

    return {
        "cycle_id": latest,
        "by_status": [
            {"target_type": t, "status": s, "count": n} for (t, s), n in sorted(by_status.items())
        ],
        "lifetime_by_status": lifetime,
        "mix": [
            {
                "target_type": t,
                "action": ACTION_LABEL.get(a, a),
                "channel": CHANNEL_LABEL.get(c, c),
                "count": n,
            }
            for (t, a, c), n in by_mix.most_common(12)
        ],
        "gate_reasons": [
            {"code": code, "label": gates.GATE_TEXT.get(code, code), "count": n}
            for code, n in reasons.most_common()
        ],
    }


def engagement_summary(db: Session) -> dict:
    """Response by channel, engine-sent versus the earlier blast programme (attribution proxy)."""
    cells: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0])  # sent, engaged, filled
    for target_type, source, channel, outcome, n in db.execute(
        select(
            Interaction.target_type,
            Interaction.source,
            Interaction.channel,
            Interaction.outcome,
            func.count(),
        ).group_by(
            Interaction.target_type, Interaction.source, Interaction.channel, Interaction.outcome
        )
    ):
        if outcome == Outcome.PENDING:
            continue
        for key in ((target_type, source, channel), (target_type, source, "all")):
            cells[key][0] += n
            cells[key][1] += n if outcome not in QUIET else 0
            cells[key][2] += n if outcome == Outcome.FILLED else 0
    rows = [
        {
            "target_type": t,
            "source": "engine" if s == "nba" else "baseline",
            "channel": c,
            "channel_label": CHANNEL_LABEL.get(c, "All channels"),
            "sent": sent,
            "engaged": engaged,
            "response_rate": _rate(engaged, sent),
            "fill_rate": _rate(filled, sent) if t == TargetType.PATIENT else None,
        }
        for (t, s, c), (sent, engaged, filled) in sorted(cells.items())
    ]
    pending = db.scalar(
        select(func.count()).select_from(Interaction).where(Interaction.outcome == Outcome.PENDING)
    )
    return {
        "by_channel": rows,
        "awaiting_response": pending,
        "like_for_like": like_for_like(db),
    }


def like_for_like(db: Session) -> list[dict]:
    """Patient outreach compared on equal terms: only touches made while the patient was
    already out of medication. The engine concentrates on these harder cases, so comparing
    it with all earlier outreach (mostly routine reminders) would understate it.
    """
    rows = patient_training_rows(load_population(db), get_config(db, "pdc_window_days"))
    result = []
    for source, label in (("history", "baseline"), ("nba", "engine")):
        subset = [r for r in rows if r["source"] == source and r["gap_days"] > 0]
        n = len(subset)
        result.append(
            {
                "source": label,
                "sent": n,
                "response_rate": _rate(sum(r["engaged"] for r in subset), n),
                "fill_rate": _rate(sum(r["filled"] for r in subset), n),
            }
        )
    return result


def adherence_summary(db: Session) -> dict:
    today = clock.get_today(db)
    threshold = get_config(db, "pdc_threshold")
    series: dict[tuple, list] = defaultdict(list)
    for as_of, measure, pdc, gap in db.execute(
        select(
            AdherenceSnapshot.as_of_date,
            PatientTherapy.measure,
            AdherenceSnapshot.pdc,
            AdherenceSnapshot.gap_days,
        ).join(PatientTherapy, PatientTherapy.id == AdherenceSnapshot.therapy_id)
    ):
        series[(as_of, measure)].append((pdc, gap))
        series[(as_of, "all")].append((pdc, gap))
    trend = [
        {
            "as_of_date": as_of,
            "measure": measure,
            "therapies": len(values),
            "adherent_rate": _rate(sum(1 for p, _ in values if p >= threshold), len(values)),
            "mean_pdc": round(sum(p for p, _ in values) / len(values), 4),
            "in_gap_rate": _rate(sum(1 for _, g in values if g > 0), len(values)),
        }
        for (as_of, measure), values in sorted(series.items())
    ]
    by_risk = dict(
        db.execute(select(Patient.risk_segment, func.count()).group_by(Patient.risk_segment)).all()
    )
    return {
        "as_of_date": today,
        "pdc_threshold": threshold,
        "patients_by_risk": by_risk,
        "current": [t for t in trend if t["as_of_date"] == today],
        "trend": trend,
    }


def hcp_summary(db: Session) -> dict:
    segments = Counter(db.scalars(select(Hcp.segment)))
    return {
        "by_segment": [{"segment": s, "count": n} for s, n in sorted(segments.items(), key=str)]
    }


def cycles(db: Session) -> list[dict]:
    rows = db.scalars(select(EngineCycle).order_by(EngineCycle.id))
    return [{"id": c.id, "as_of_date": c.as_of_date, "stats": c.stats} for c in rows]


def overview(db: Session) -> dict:
    return {
        "as_of_date": clock.get_today(db),
        "recommendations": recommendation_summary(db),
        "engagement": engagement_summary(db),
        "adherence": adherence_summary(db),
        "hcps": hcp_summary(db),
        "cycles": cycles(db),
    }
