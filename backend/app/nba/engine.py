"""The next-best-action cycle.

For every target: list the candidate touches, run the hard gates, score what is left,
pick one, explain it, and write the audit trail. Models rank; gates decide eligibility.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit, pipeline
from app.features import engagement as eng
from app.features.adherence import Adherence, compute_adherence, coverage_intervals
from app.features.population import Population, load_population
from app.models import Content, EngineCycle, Nba, NbaCandidate, PatientTherapy
from app.models.enums import ActionType, NbaStatus, RiskSegment, TargetType
from app.models.tables import utcnow
from app.nba import gates, rationale
from app.scoring import propensity
from app.scoring.risk import Risk, score_risk

CANDIDATES_KEPT = 10
# Recommendations from an earlier cycle that nobody acted on are replaced, not stacked.
SUPERSEDED = (NbaStatus.GENERATED, NbaStatus.READY_FOR_REVIEW, NbaStatus.BLOCKED)
IN_FLIGHT = (NbaStatus.APPROVED,)


@dataclass
class Candidate:
    target_type: str
    target_id: str
    action: str
    channel: str
    content: Content
    features: dict
    failures: list[str]
    p_engage: float = 0.0
    p_outcome: float = 0.0
    score: float = 0.0

    @property
    def eligible(self) -> bool:
        return not self.failures


@dataclass
class PatientContext:
    therapy: PatientTherapy
    adherence: Adherence
    risk: Risk
    state: eng.EngagementState
    runout: date | None


@dataclass
class CycleResult:
    cycle: EngineCycle
    stats: dict = field(default_factory=dict)


# --- Candidate generation ----------------------------------------------------


def _needs_action(adherence: Adherence, risk: Risk, runout: date | None, today: date, due: int):
    if risk.segment != RiskSegment.LOW or adherence.gap_days > 0:
        return True
    due_soon = runout is not None and (runout - today).days <= due
    return due_soon and adherence.mean_lateness >= 2


def patient_context(pop: Population, pid: str, settings: dict) -> PatientContext | None:
    """The therapy most in need of action for this patient, or None if none needs any."""
    state = eng.patient_state(pop, pid)
    best: PatientContext | None = None
    for therapy in pop.therapies.get(pid, []):
        fills = pop.fills.get(therapy.id, [])
        adherence = compute_adherence(
            fills, therapy.start_date, pop.as_of, settings["pdc_window_days"]
        )
        risk = score_risk(adherence, state.unresponsive_share(), settings)
        runout = coverage_intervals(fills)[-1][1] if fills else None
        if not _needs_action(adherence, risk, runout, pop.as_of, settings["nba_due_soon_days"]):
            continue
        if best is None or risk.score > best.risk.score:
            best = PatientContext(therapy, adherence, risk, state, runout)
    return best


def patient_candidates(
    pop: Population, pid: str, ctx: PatientContext, settings: dict
) -> list[Candidate]:
    patient = pop.patients[pid]
    out = []
    for content in pop.contents.values():
        if content.audience != TargetType.PATIENT:
            continue
        if content.measure not in (ctx.therapy.measure, None):
            continue
        if content.action_type == ActionType.REFILL_NUDGE and ctx.adherence.never_filled:
            continue
        for channel in content.channels:
            out.append(
                Candidate(
                    TargetType.PATIENT,
                    pid,
                    content.action_type,
                    channel,
                    content,
                    eng.patient_features(
                        patient,
                        ctx.therapy,
                        ctx.adherence,
                        ctx.state,
                        pop.as_of,
                        channel,
                        content.action_type,
                    ),
                    gates.evaluate(
                        TargetType.PATIENT,
                        content,
                        channel,
                        pop.as_of,
                        pop.consents.get(pid, []),
                        ctx.state,
                        settings["frequency_caps"],
                    ),
                )
            )
    return out


def hcp_candidates(
    pop: Population, hcp_id: str, state: eng.EngagementState, settings: dict
) -> list[Candidate]:
    hcp = pop.hcps[hcp_id]
    history = pop.interactions.get((TargetType.HCP, hcp_id), [])
    already_engaged = {i.content_id for i in history if eng.is_engaged(i.outcome)}
    out = []
    for content in pop.contents.values():
        if content.audience != TargetType.HCP or content.content_id in already_engaged:
            continue
        # Content for a specialty goes only to HCPs who hold it (an HCP may hold several).
        held = pop.hcp_specialties.get(hcp_id) or {hcp.specialty}
        if content.specialty is not None and content.specialty not in held:
            continue
        for channel in content.channels:
            out.append(
                Candidate(
                    TargetType.HCP,
                    hcp_id,
                    content.action_type,
                    channel,
                    content,
                    eng.hcp_features(hcp, state, pop.as_of, channel, content),
                    gates.evaluate(
                        TargetType.HCP,
                        content,
                        channel,
                        pop.as_of,
                        [],
                        state,
                        settings["frequency_caps"],
                    ),
                )
            )
    return out


# --- Scoring -----------------------------------------------------------------


def score_patient_candidates(db: Session, candidates: list[Candidate], settings: dict) -> None:
    if not candidates:
        return
    rows = [c.features for c in candidates]
    engage_model = propensity.load_active(db, pipeline.PATIENT_ENGAGE.name)
    fill_model = propensity.load_active(db, pipeline.PATIENT_FILL.name)
    # Without a trained model the smoothed historical rates stand in for it.
    p_engage = (
        propensity.predict(engage_model, pipeline.PATIENT_ENGAGE, rows)
        if engage_model
        else [r["rate_channel"] for r in rows]
    )
    p_fill = (
        propensity.predict(fill_model, pipeline.PATIENT_FILL, rows)
        if fill_model
        else [r["fill_rate_action"] for r in rows]
    )
    cost = settings["nba_channel_cost"]
    for c, pe, pf in zip(candidates, p_engage, p_fill, strict=True):
        c.p_engage, c.p_outcome = float(pe), float(pf)
        c.score = round(
            100 * (0.8 * c.p_outcome + 0.2 * c.p_engage) - cost.get(c.channel, 0),
            2,
        )


def score_hcp_candidates(
    db: Session, pop: Population, candidates: list[Candidate], settings: dict
) -> None:
    if not candidates:
        return
    rows = [c.features for c in candidates]
    model = propensity.load_active(db, pipeline.HCP_ENGAGE.name)
    p_engage = (
        propensity.predict(model, pipeline.HCP_ENGAGE, rows)
        if model
        else [r["rate_channel"] for r in rows]
    )
    cost = settings["nba_channel_cost"]
    repeat_after = pop.as_of - timedelta(days=settings["nba_repeat_content_days"])
    recently_sent: dict[str, set] = {}
    for c, pe in zip(candidates, p_engage, strict=True):
        if c.target_id not in recently_sent:
            recently_sent[c.target_id] = {
                i.content_id
                for i in pop.interactions.get((TargetType.HCP, c.target_id), [])
                if i.int_ts.date() >= repeat_after
            }
        c.p_engage = c.p_outcome = float(pe)
        score = 100 * c.p_engage - cost.get(c.channel, 0)
        if c.content.content_id in recently_sent[c.target_id]:
            score *= 0.5
        c.score = round(score, 2)


def rank(candidates: list[Candidate]) -> list[Candidate]:
    """Best first. Ties go to measure-specific content, then to the lower content id."""
    return sorted(
        candidates,
        key=lambda c: (-c.score, c.content.measure is None, c.content.content_id, c.channel),
    )


# --- Selection ---------------------------------------------------------------


@dataclass
class Decision:
    chosen: Candidate | None
    status: str | None
    withheld: dict | None
    ranked: list[Candidate]
    outcome: str  # ready | blocked | deferred | none


def decide(candidates: list[Candidate], settings: dict, min_score: float = 0.0) -> Decision:
    ranked = rank(candidates)
    if not ranked:
        return Decision(None, None, None, ranked, "none")
    # Contact limits apply to the target, not the message: wait, and say nothing is blocked.
    if all(gates.frequency_limited(c.failures) for c in ranked):
        return Decision(None, None, None, ranked, "deferred")
    eligible = [c for c in ranked if c.eligible]
    top = ranked[0]

    if not eligible:
        # Nothing may be sent. Record the best option and why it is blocked.
        return Decision(top, NbaStatus.BLOCKED, None, ranked, "blocked")

    best = eligible[0]
    if best.score < min_score:
        return Decision(None, None, None, ranked, "none")

    withheld = None
    if not top.eligible and top.score >= best.score * settings["nba_withheld_margin"]:
        withheld = {
            "content_id": top.content.content_id,
            "title": top.content.title,
            "action": str(top.action),
            "channel": str(top.channel),
            "score": top.score,
            "failures": top.failures,
        }
    return Decision(best, NbaStatus.READY_FOR_REVIEW, withheld, ranked, "ready")


# --- Persistence -------------------------------------------------------------


def _persist(
    db: Session,
    cycle: EngineCycle,
    decision: Decision,
    reasons: list[dict],
    scheduled_for: date,
    timing_note: str,
    priority: float,
    therapy_id: int | None,
    as_of: date,
) -> Nba:
    c = decision.chosen
    blocked = decision.status == NbaStatus.BLOCKED
    block_reason = gates.describe(c.failures) if blocked else None
    nba = Nba(
        cycle_id=cycle.id,
        target_type=c.target_type,
        target_id=c.target_id,
        therapy_id=therapy_id,
        action=c.action,
        channel=c.channel,
        scheduled_for=scheduled_for,
        timing_note=timing_note,
        rationale=rationale.to_text(reasons),
        reason_codes=reasons,
        content_id=c.content.content_id,
        score=c.score,
        priority=round(priority, 2),
        predicted={"p_engage": round(c.p_engage, 4), "p_outcome": round(c.p_outcome, 4)},
        withheld=decision.withheld,
        status=decision.status,
        block_reason=block_reason,
        as_of_date=as_of,
    )
    db.add(nba)
    db.flush()
    for position, cand in enumerate(decision.ranked[:CANDIDATES_KEPT], start=1):
        db.add(
            NbaCandidate(
                cycle_id=cycle.id,
                nba_id=nba.id,
                target_type=cand.target_type,
                target_id=cand.target_id,
                action=cand.action,
                channel=cand.channel,
                content_id=cand.content.content_id,
                eligible=cand.eligible,
                gate_failures=cand.failures,
                score=cand.score,
                rank=position,
            )
        )
    audit.record(
        db,
        "nba_blocked" if blocked else "nba_generated",
        "nba",
        nba.id,
        nba_id=nba.id,
        compliance_ok=gates.compliance_ok(c.failures),
        consent_ok=gates.consent_ok(c.failures) if c.target_type == TargetType.PATIENT else None,
        reason=block_reason or "All gates passed",
        detail={
            "target": f"{c.target_type}:{c.target_id}",
            "content_id": c.content.content_id,
            "channel": str(c.channel),
            "gate_failures": c.failures,
            "withheld": decision.withheld,
        },
    )
    return nba


# --- Cycle -------------------------------------------------------------------


def _expire_superseded(db: Session) -> int:
    result = db.execute(
        update(Nba).where(Nba.status.in_(SUPERSEDED)).values(status=NbaStatus.EXPIRED)
    )
    return result.rowcount or 0


def run_cycle(db: Session, pop: Population | None = None) -> CycleResult:
    """Generates one recommendation per target that needs one. Caller commits."""
    pop = pop or load_population(db)
    settings = pipeline.engine_settings(db)
    stats: Counter = Counter()
    stats["expired_superseded"] = _expire_superseded(db)

    cycle = EngineCycle(as_of_date=pop.as_of)
    db.add(cycle)
    db.flush()

    in_flight = {
        (t, i)
        for t, i in db.execute(
            select(Nba.target_type, Nba.target_id).where(Nba.status.in_(IN_FLIGHT))
        )
    }

    # Patients: build and score every candidate in one batch, then decide per patient.
    contexts: dict[str, PatientContext] = {}
    by_patient: dict[str, list[Candidate]] = {}
    for pid in pop.patients:
        if (TargetType.PATIENT, pid) in in_flight:
            stats["patient_in_flight"] += 1
            continue
        ctx = patient_context(pop, pid, settings)
        if ctx is None:
            stats["patient_no_action_needed"] += 1
            continue
        contexts[pid] = ctx
        by_patient[pid] = patient_candidates(pop, pid, ctx, settings)
    score_patient_candidates(db, [c for cs in by_patient.values() for c in cs], settings)

    for pid, candidates in by_patient.items():
        ctx = contexts[pid]
        decision = decide(candidates, settings)
        stats[f"patient_{decision.outcome}"] += 1
        if decision.chosen is None:
            continue
        c = decision.chosen
        preferred = pop.patients[pid].preferred_channel
        if (
            decision.outcome == "ready"
            and decision.withheld is None
            and preferred
            and preferred != c.channel
            and not gates.has_consent(pop.consents.get(pid, []), preferred, pop.as_of)
        ):
            # The patient asked for a channel they have not consented to: say so explicitly.
            decision.withheld = {
                "kind": "preferred_channel",
                "channel": str(preferred),
                "failures": [gates.CONSENT_MISSING],
            }
        scheduled_for, timing_note = rationale.patient_timing(ctx.adherence, pop.as_of, ctx.runout)
        predicted = {"p_engage": c.p_engage, "p_outcome": c.p_outcome}
        reasons = rationale.patient_reasons(
            drug=ctx.therapy.drug_name,
            adherence=ctx.adherence,
            risk=ctx.risk,
            state=ctx.state,
            action=c.action,
            channel=c.channel,
            preferred_channel=pop.patients[pid].preferred_channel,
            copay=ctx.therapy.copay,
            content=c.content,
            predicted=predicted,
            timing_note=timing_note,
            withheld=decision.withheld,
            eligible=c.eligible,
        )
        priority = ctx.risk.score * max(c.p_outcome, 0.01)
        _persist(
            db, cycle, decision, reasons, scheduled_for, timing_note, priority,
            ctx.therapy.id, pop.as_of,
        )  # fmt: skip
        stats["patient_withheld"] += decision.withheld is not None

    # HCPs.
    states = {hcp_id: eng.hcp_state(pop, hcp_id) for hcp_id in pop.hcps}
    by_hcp: dict[str, list[Candidate]] = {}
    for hcp_id in pop.hcps:
        if (TargetType.HCP, hcp_id) in in_flight:
            stats["hcp_in_flight"] += 1
            continue
        by_hcp[hcp_id] = hcp_candidates(pop, hcp_id, states[hcp_id], settings)
    score_hcp_candidates(db, pop, [c for cs in by_hcp.values() for c in cs], settings)

    for hcp_id, candidates in by_hcp.items():
        hcp, state = pop.hcps[hcp_id], states[hcp_id]
        decision = decide(candidates, settings, min_score=settings["nba_hcp_min_score"])
        stats[f"hcp_{decision.outcome}"] += 1
        if decision.chosen is None:
            continue
        c = decision.chosen
        scheduled_for, timing_note = rationale.hcp_timing(state, pop.as_of)
        measure, subtopic = eng.topic_parts(c.content)
        reasons = rationale.hcp_reasons(
            hcp=hcp,
            state=state,
            channel=c.channel,
            content=c.content,
            measure=measure,
            subtopic=subtopic,
            predicted={"p_engage": c.p_engage},
            timing_note=timing_note,
            withheld=decision.withheld,
            eligible=c.eligible,
        )
        # An unrated (invited) HCP counts as mid-value: neither favoured nor buried.
        value = hcp.value_score if hcp.value_score is not None else 50.0
        priority = value * max(c.p_engage, 0.01)
        _persist(
            db, cycle, decision, reasons, scheduled_for, timing_note, priority, None, pop.as_of
        )
        stats["hcp_withheld"] += decision.withheld is not None

    cycle.finished_ts = utcnow()
    cycle.stats = dict(sorted(stats.items()))
    audit.record(
        db,
        "cycle_completed",
        "engine_cycle",
        cycle.id,
        reason=f"Cycle for {pop.as_of}",
        detail=cycle.stats,
    )
    db.flush()
    return CycleResult(cycle, cycle.stats)
