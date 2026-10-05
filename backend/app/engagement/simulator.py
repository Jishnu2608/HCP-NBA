"""Plays the synthetic world forward to the real date.

This is the only runtime code that reads the hidden traits in sim_latent. It stands in for
reality: people answer (or ignore) what was sent and refill (or do not) on their own
schedule. The engine sees none of this directly, only the interactions and fills it leaves.
"""

from collections import Counter
from datetime import date, datetime, time, timedelta
from random import Random

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import cycle
from app.core import clock
from app.core.config import get_settings
from app.datagen import behavior
from app.engagement.delivery import capture_response, record_fill
from app.models import Content, Interaction, MedicationFill, PatientTherapy, SimLatent
from app.models.enums import Channel, FillSource, Outcome, TargetType
from app.models.tables import utcnow

DIGITAL_OUTCOMES = [(Outcome.OPENED, 0.55), (Outcome.CLICKED, 0.35), (Outcome.REPLIED, 0.10)]
ACTOR, ACTOR_ROLE = "simulator", "system"


def _rng(*parts) -> Random:
    return Random(":".join(str(p) for p in (get_settings().datagen_seed, "sim", *parts)))


def _recent_touches(db: Session, i: Interaction, window: int) -> int:
    since = i.int_ts - timedelta(days=window)
    return len(
        db.scalars(
            select(Interaction.id).where(
                Interaction.target_type == i.target_type,
                Interaction.target_id == i.target_id,
                Interaction.id != i.id,
                Interaction.int_ts >= since,
                Interaction.int_ts <= i.int_ts,
            )
        ).all()
    )


def schedule_next_fill(latent: SimLatent, therapy_id: int, fill_day: date, fill_count: int) -> None:
    """After a fill, decide (hidden) when this patient would next refill unprompted."""
    traits = dict(latent.traits)
    state = dict(traits["therapies"][str(therapy_id)])
    rng = _rng("next", therapy_id, fill_day)
    runout = fill_day + timedelta(days=state["days_supply"])
    k = max(0, fill_count - 1)
    if rng.random() < behavior.discontinue_hazard(traits, k, state["copay"]):
        state["next_fill"] = None
    else:
        delay = behavior.natural_refill_delay(rng, traits, k, state["copay"])
        state["next_fill"] = (runout + timedelta(days=delay)).isoformat()
    traits["therapies"] = {**traits["therapies"], str(therapy_id): state}
    latent.traits = traits


def _fill_count(db: Session, therapy_id: int) -> int:
    return len(
        db.scalars(select(MedicationFill.id).where(MedicationFill.therapy_id == therapy_id)).all()
    )


def apply_fill(
    db: Session, therapy: PatientTherapy, when: datetime, source: str = FillSource.CLAIMS
) -> None:
    """A fill happened (prompted or not): store it and, for a simulated patient, reschedule
    the hidden next refill. Real patients have no hidden traits and are never simulated."""
    record_fill(db, therapy, when, source)
    db.flush()
    latent = db.get(SimLatent, (TargetType.PATIENT, therapy.patient_id))
    if latent is not None and str(therapy.id) in latent.traits.get("therapies", {}):
        schedule_next_fill(latent, therapy.id, when.date(), _fill_count(db, therapy.id))


def _resolve_patient(db: Session, i: Interaction, today: date, stats: Counter) -> None:
    latent = db.get(SimLatent, (TargetType.PATIENT, i.target_id))
    traits = latent.traits
    rng = _rng("resp", i.id)
    if rng.random() >= behavior.patient_engage_prob(traits, i.channel, _recent_touches(db, i, 14)):
        capture_response(db, i, Outcome.NO_RESPONSE, i.int_ts, actor=ACTOR, actor_role=ACTOR_ROLE)
        stats["patient_no_response"] += 1
        return
    therapy = db.get(PatientTherapy, i.therapy_id) if i.therapy_id else None
    state = traits["therapies"].get(str(i.therapy_id)) if therapy else None
    discontinued = bool(state) and state["next_fill"] is None
    fill_day = None
    already_filled = therapy is not None and db.scalar(
        select(MedicationFill.id).where(
            MedicationFill.therapy_id == therapy.id, MedicationFill.fill_date > i.int_ts.date()
        )
    )
    if (
        therapy
        and not already_filled
        and rng.random() < behavior.patient_fill_prob(traits, i.type, discontinued)
    ):
        fill_day = i.int_ts.date() + timedelta(days=rng.randint(1, 4))
    if fill_day and fill_day <= today:
        when = datetime.combine(fill_day, time(12, 0))
        apply_fill(db, therapy, when)
        capture_response(db, i, Outcome.FILLED, when, actor=ACTOR, actor_role=ACTOR_ROLE)
        stats["patient_filled"] += 1
        return
    natural = state["next_fill"] if state else None
    if fill_day and (natural is None or fill_day.isoformat() < natural):
        # Persuaded, but the pharmacy trip is still ahead: it happens when that day comes.
        updated = dict(traits)
        updated["therapies"] = {
            **traits["therapies"],
            str(therapy.id): {**state, "next_fill": fill_day.isoformat()},
        }
        latent.traits = updated
    outcome = (
        Outcome.COMPLETED
        if i.channel == Channel.PHONE
        else behavior.weighted_choice(rng, DIGITAL_OUTCOMES)
    )
    when = min(i.int_ts + timedelta(hours=rng.randint(1, 20)), _now())
    capture_response(db, i, outcome, when, actor=ACTOR, actor_role=ACTOR_ROLE)
    stats["patient_engaged"] += 1


def _resolve_hcp(db: Session, i: Interaction, stats: Counter) -> None:
    traits = db.get(SimLatent, (TargetType.HCP, i.target_id)).traits
    content = db.get(Content, i.content_id)
    rng = _rng("resp", i.id)
    p = behavior.hcp_engage_prob(traits, i.channel, content.topic, _recent_touches(db, i, 30))
    if rng.random() < p:
        outcome = (
            Outcome.COMPLETED
            if i.channel == Channel.REP_VISIT
            else behavior.weighted_choice(rng, DIGITAL_OUTCOMES)
        )
        when = min(i.int_ts + timedelta(hours=rng.randint(1, 30)), _now())
        stats["hcp_engaged"] += 1
    else:
        outcome = Outcome.DECLINED if i.channel == Channel.REP_VISIT else Outcome.NO_RESPONSE
        when = i.int_ts
        stats["hcp_no_response"] += 1
    capture_response(db, i, outcome, when, actor=ACTOR, actor_role=ACTOR_ROLE)


def _natural_fills(db: Session, after: date, until: date, stats: Counter) -> None:
    """Refills simulated patients make on their own after one date, up to another."""
    therapies = {t.id: t for t in db.scalars(select(PatientTherapy))}
    for latent in db.scalars(select(SimLatent).where(SimLatent.target_type == TargetType.PATIENT)):
        for therapy_id in list(latent.traits.get("therapies", {})):
            for _ in range(12):  # bounded: at most a dozen refills per therapy per advance
                due = latent.traits["therapies"][therapy_id]["next_fill"]
                if due is None or not after < date.fromisoformat(due) <= until:
                    break
                when = datetime.combine(date.fromisoformat(due), time(12, 0))
                apply_fill(db, therapies[int(therapy_id)], when)
                stats["natural_fills"] += 1


def _now() -> datetime:
    return datetime.combine(clock.today(), utcnow().time().replace(microsecond=0))


def catch_up(db: Session) -> dict:
    """Plays the synthetic population forward to today: the refills simulated patients make
    on their own since the simulator last ran. Nothing happens to real people."""
    today = clock.today()
    since = clock.simulated_through(db) or today
    stats: Counter = Counter()
    if since < today:
        _natural_fills(db, since, today, stats)
    clock.set_simulated_through(db, max(since, today))
    return {"from": since, "to": today, **stats}


def play_out(db: Session, *, retrain: bool = True) -> dict:
    """Resolves what was sent to simulated people now (as they would respond within hours),
    catches up their own refills to today, and reruns the engine. The date is never moved:
    a persuaded refill that falls after today happens when that day really comes."""
    today = clock.today()
    stats: Counter = Counter()
    # Outreach lands first, exactly as in the seeded history: if it persuades the patient,
    # that fill replaces the refill they would otherwise have made on their own later.
    pending = db.scalars(
        select(Interaction).where(Interaction.outcome == Outcome.PENDING).order_by(Interaction.id)
    ).all()
    simulated = {(t, i) for t, i in db.execute(select(SimLatent.target_type, SimLatent.target_id))}
    for i in pending:
        if (i.target_type, i.target_id) not in simulated:
            # A real person answers for themselves in their inbox; nothing is invented.
            continue
        if i.target_type == TargetType.PATIENT:
            _resolve_patient(db, i, today, stats)
        else:
            _resolve_hcp(db, i, stats)
    caught = catch_up(db)
    stats["natural_fills"] += caught.get("natural_fills", 0)
    result = cycle.run(db, retrain=retrain)
    return {
        "as_of_date": today,
        "responses": dict(sorted(stats.items())),
        "cycle_id": result.cycle.id,
        "cycle": result.stats,
    }
