"""A representative proposing contact with a chosen HCP (MR-1).

Nothing here decides eligibility on its own: options and proposals go through the engine's own
candidate generation (`engine.hcp_candidates`: approved content only, audience, the HCP's
specialties, a lineage the HCP declined left out) and the deterministic gates (MLR status and
dates, channel, jurisdiction, contact frequency). A proposal becomes an ordinary recommendation
(`origin="rep"`) that goes through the same review, governed wording and send-time re-check as
one the engine chose.
"""

from datetime import date, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, pipeline
from app.core import clock
from app.core.engine_config import get_config
from app.features import engagement as eng
from app.features.population import load_population
from app.models import EngineCycle, Hcp, Nba, User
from app.models.enums import NbaStatus, TargetType
from app.nba import engine, gates, rationale

HORIZON_DAYS = 120


def _error(code: str, message: str, http: int = status.HTTP_409_CONFLICT, **extra):
    return HTTPException(http, {"code": code, "message": message, **extra})


def contact_window(db: Session, hcp: Hcp, state: eng.EngagementState | None = None) -> dict:
    """Whether the contact limits allow another touch today, and if not, from when."""
    today = clock.get_today(db)
    if state is None:
        state = eng.hcp_state(load_population(db), hcp.hcp_id)
    cap = get_config(db, "frequency_caps")["HCP"]
    last = state.days[-1] if state.days else None
    failures = gates.frequency_failures(state, today, cap)
    next_allowed = today
    while gates.frequency_failures(state, next_allowed, cap) and next_allowed < today + timedelta(
        days=HORIZON_DAYS
    ):
        next_allowed += timedelta(days=1)
    reason = None
    if failures:
        reason = gates.describe(failures)
        if "min_gap" in failures and last:
            reason += f": last contact {last:%d %b}, minimum gap {cap['min_gap_days']} days"
        if "frequency_cap" in failures:
            reason += f" ({cap['max_touches']} contacts per {cap['window_days']} days)"
    return {
        "allowed_now": not failures,
        "next_allowed": next_allowed,
        "last_contact": last,
        "reason": reason,
        "min_gap_days": cap["min_gap_days"],
    }


def _candidates(db: Session, hcp_id: str):
    pop = load_population(db)
    if hcp_id not in pop.hcps:
        raise _error(
            "not_in_engine",
            "This HCP is not open to engagement now (inactive account or not part of the "
            "engagement population).",
        )
    settings = pipeline.engine_settings(db)
    state = eng.hcp_state(pop, hcp_id)
    candidates = engine.hcp_candidates(pop, hcp_id, state, settings)
    engine.score_hcp_candidates(db, pop, candidates, settings)
    return pop, settings, state, candidates


def contact_options(db: Session, hcp: Hcp) -> dict:
    """Every approved content option for this HCP, with what the gates say about it now."""
    pop, _, state, candidates = _candidates(db, hcp.hcp_id)
    options = []
    for c in engine.rank(candidates):
        if c.content.mlr_status != "approved":
            continue
        options.append(
            {
                "content_id": c.content.content_id,
                "version": c.content.version,
                "title": c.content.title,
                "product": c.content.product,
                "action": c.action,
                "channel": c.channel,
                "eligible": c.eligible,
                "failures": c.failures,
                "reason": gates.describe(c.failures) if c.failures else None,
                "frequency_only": bool(c.failures)
                and gates.frequency_limited(c.failures)
                and all(f in gates.FREQUENCY_CODES for f in c.failures),
                "p_engage": round(c.p_engage, 3),
            }
        )
    return {"window": contact_window(db, hcp, state), "options": options}


def propose(db: Session, user: User, hcp: Hcp, content_id: str, channel: str):
    """Creates a recommendation for review, only if every gate passes now."""
    pop, settings, state, candidates = _candidates(db, hcp.hcp_id)
    chosen = next(
        (c for c in candidates if c.content.content_id == content_id and c.channel == channel),
        None,
    )
    window = contact_window(db, hcp, state)
    if chosen is None or chosen.content.mlr_status != "approved":
        raise _error(
            "contact_not_allowed",
            "This content cannot be used with this HCP on this channel: it is not approved, "
            "not for their specialty or audience, or they already engaged with or declined it.",
            failures=["not_an_option"],
            next_allowed=window["next_allowed"].isoformat(),
        )
    if chosen.failures:
        raise _error(
            "contact_not_allowed",
            f"Not allowed now: {gates.describe(chosen.failures)}."
            + (
                f" Next contact possible from {window['next_allowed']:%d %b %Y}."
                if gates.frequency_limited(chosen.failures)
                else ""
            ),
            failures=chosen.failures,
            next_allowed=window["next_allowed"].isoformat(),
        )
    open_nba = engine.open_hcp_recommendation(db, hcp.hcp_id)
    if open_nba is not None:
        raise _error(
            "recommendation_open",
            "This HCP already has a recommendation you proposed or approved: finish it first.",
            nba_id=open_nba,
        )
    # The engine's own suggestion for this HCP makes way for the representative's choice.
    for other in db.scalars(
        select(Nba).where(
            Nba.target_type == TargetType.HCP,
            Nba.target_id == hcp.hcp_id,
            Nba.status == NbaStatus.READY_FOR_REVIEW,
        )
    ):
        other.status = NbaStatus.EXPIRED
        other.block_reason = f"Replaced by a contact {user.display_name} proposed"
    cycle = db.scalar(select(EngineCycle).order_by(EngineCycle.id.desc()))
    if cycle is None:
        raise _error("no_cycle", "The engine has not run yet.")
    today = clock.get_today(db)
    measure, subtopic = eng.topic_parts(chosen.content)
    reasons = [
        rationale.proposal_reason(user.display_name),
        *engine.intent_reasons(pop, hcp.hcp_id),
        *rationale.hcp_reasons(
            hcp=pop.hcps[hcp.hcp_id],
            state=state,
            channel=chosen.channel,
            content=chosen.content,
            measure=measure,
            subtopic=subtopic,
            predicted={"p_engage": chosen.p_engage},
            timing_note=rationale.hcp_timing(state, today)[1],
            withheld=None,
            eligible=True,
        ),
    ]
    value = hcp.value_score if hcp.value_score is not None else 50.0
    decision = engine.Decision(chosen, NbaStatus.READY_FOR_REVIEW, None, [chosen], "ready")
    nba = engine._persist(
        db, cycle, decision, reasons, today, rationale.hcp_timing(state, today)[1],
        value * max(chosen.p_engage, 0.01), None, today, origin="rep",
    )  # fmt: skip
    from app.llm.service import draft_for_nba

    draft_for_nba(db, nba, actor=user.username, actor_role=user.role)
    audit.record(
        db,
        "nba_proposed_by_rep",
        "nba",
        nba.id,
        nba_id=nba.id,
        actor=user.username,
        actor_role=user.role,
        compliance_ok=True,
        reason=f"Proposed from HCP 360: {content_id} by {channel}",
        detail={"hcp_id": hcp.hcp_id, "content_id": content_id, "channel": channel},
    )
    return nba


def next_allowed(db: Session, hcp: Hcp) -> date:
    return contact_window(db, hcp)["next_allowed"]


__all__ = ["contact_options", "contact_window", "next_allowed", "propose", "TargetType"]
