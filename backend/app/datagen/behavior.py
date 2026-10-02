"""Hidden behaviour model: how synthetic people really act.

Used by the data generator (history) and the outcome simulator (after a send), so that
what happens after a recommendation is consistent with what happened before it. The
engine never sees these traits; it can only learn them from observed outcomes.
"""

from random import Random

from app.datagen.reference import HCP_CHANNELS, PATIENT_CHANNELS, PRIMARY_CARE, SUBTOPICS
from app.models.enums import Measure

ARCHETYPES = [
    ("steady", 0.42),
    ("forgetful", 0.22),
    ("drifter", 0.16),
    ("cost_barrier", 0.13),
    ("never_starter", 0.07),
]

# archetype -> probability that an engaged outreach of this type leads to a prompt fill
_ACTION_EFFECT = {
    "steady": {"refill_nudge": 0.55, "check_in": 0.50, "education": 0.40, "cost_support": 0.30},
    "forgetful": {"refill_nudge": 0.75, "check_in": 0.60, "education": 0.20, "cost_support": 0.10},
    "drifter": {"refill_nudge": 0.22, "check_in": 0.55, "education": 0.45, "cost_support": 0.10},
    "cost_barrier": {
        "refill_nudge": 0.08, "check_in": 0.30, "education": 0.10, "cost_support": 0.70,
    },
    "never_starter": {
        "refill_nudge": 0.10, "check_in": 0.35, "education": 0.42, "cost_support": 0.30,
    },
}  # fmt: skip


def _clamp(x: float, lo: float = 0.03, hi: float = 0.92) -> float:
    return round(max(lo, min(hi, x)), 3)


def weighted_choice(rng: Random, pairs):
    """pairs: iterable of (value, weight)."""
    values, weights = zip(*pairs, strict=True)
    return rng.choices(values, weights=weights, k=1)[0]


# --- Patients ----------------------------------------------------------------


def make_patient_traits(rng: Random, age: int) -> dict:
    archetype = weighted_choice(rng, ARCHETYPES)
    if age < 60:
        base = {"sms": 0.55, "email": 0.40, "portal": 0.35, "phone": 0.25}
    elif age <= 72:
        base = {"sms": 0.40, "email": 0.40, "portal": 0.25, "phone": 0.40}
    else:
        base = {"sms": 0.20, "email": 0.25, "portal": 0.12, "phone": 0.55}
    favourite = weighted_choice(rng, base.items())
    channel_resp = {
        ch: _clamp(base[ch] * (1.45 if ch == favourite else 1.0) * rng.uniform(0.7, 1.25))
        for ch in PATIENT_CHANNELS
    }
    action_effect = {
        action: _clamp(p + rng.uniform(-0.1, 0.1))
        for action, p in _ACTION_EFFECT[archetype].items()
    }
    return {
        "archetype": archetype,
        "favourite_channel": favourite,
        "channel_resp": channel_resp,
        "action_effect": action_effect,
    }


def natural_refill_delay(rng: Random, traits: dict, fill_index: int, copay: float) -> int:
    """Days after supply runs out before the patient refills on their own. Negative = early."""
    archetype = traits["archetype"]
    if archetype == "steady":
        return max(-3, round(rng.gauss(0, 2)))
    if archetype == "forgetful":
        return int(rng.expovariate(1 / 9))
    if archetype == "drifter":
        return int(rng.expovariate(1 / (3 + 2.5 * fill_index)))
    if archetype == "cost_barrier":
        return int(rng.expovariate(1 / (4 + copay * 0.5)))
    return int(rng.expovariate(1 / 20))


def discontinue_hazard(traits: dict, fill_index: int, copay: float) -> float:
    """Chance the patient silently stops therapy after a given fill."""
    archetype = traits["archetype"]
    if archetype == "steady":
        return 0.004
    if archetype == "forgetful":
        return 0.015
    if archetype == "drifter":
        return min(0.35, 0.03 + 0.012 * fill_index)
    if archetype == "cost_barrier":
        return 0.02 + copay * 0.001
    return 0.6


def patient_engage_prob(traits: dict, channel: str, recent_touches: int) -> float:
    """Chance the patient engages with an outreach. Falls with touches in the last 14 days."""
    return traits["channel_resp"].get(channel, 0.05) / (1 + 0.35 * recent_touches)


def patient_fill_prob(traits: dict, action: str, discontinued: bool) -> float:
    """Given engagement, chance the outreach leads to a fill within a few days."""
    effect = traits["action_effect"].get(action, 0.1)
    return effect * (0.5 if discontinued else 1.0)


# --- HCPs --------------------------------------------------------------------


def make_hcp_traits(rng: Random, specialty: str) -> dict:
    if specialty == "Endocrinology":
        measure_base = {"diabetes": 0.9, "hypertension": 0.35, "cholesterol": 0.45}
    elif specialty == "Cardiovascular Disease":
        measure_base = {"diabetes": 0.35, "hypertension": 0.85, "cholesterol": 0.85}
    else:
        measure_base = {"diabetes": 0.6, "hypertension": 0.65, "cholesterol": 0.6}
    measure_interest = {m: _clamp(p * rng.uniform(0.7, 1.2)) for m, p in measure_base.items()}

    favourite_subtopic = rng.choice(SUBTOPICS)
    subtopic_pref = {
        s: _clamp((1.0 if s == favourite_subtopic else 0.55) * rng.uniform(0.75, 1.15), hi=1.0)
        for s in SUBTOPICS
    }

    base = {"email": 0.30, "portal": 0.22, "rep_visit": 0.45, "phone": 0.12}
    if specialty not in PRIMARY_CARE:
        base["rep_visit"] = 0.35
    favourite = weighted_choice(rng, base.items())
    channel_resp = {
        ch: _clamp(base[ch] * (1.6 if ch == favourite else 1.0) * rng.uniform(0.6, 1.3))
        for ch in HCP_CHANNELS
    }
    return {
        "favourite_channel": favourite,
        "channel_resp": channel_resp,
        "measure_interest": measure_interest,
        "subtopic_pref": subtopic_pref,
        "digital_fatigue": round(rng.uniform(0.15, 0.9), 3),
    }


def split_topic(topic: str) -> tuple[str | None, str]:
    """'diabetes_outcomes' -> ('diabetes', 'outcomes'); 'general_program' -> (None, 'program')."""
    head, _, subtopic = topic.rpartition("_")
    measure = head if head in {m.value for m in Measure} else None
    return measure, subtopic


def hcp_engage_prob(traits: dict, channel: str, topic: str, recent_touches: int) -> float:
    """Chance an HCP engages. Depends on channel, topic relevance and recent contact load."""
    measure, subtopic = split_topic(topic)
    interest = traits["measure_interest"].get(measure, 0.55) if measure else 0.55
    relevance = interest * traits["subtopic_pref"].get(subtopic, 0.6)
    fatigue = 1 / (1 + traits["digital_fatigue"] * 0.5 * recent_touches)
    return min(0.95, traits["channel_resp"].get(channel, 0.05) * (0.35 + 1.3 * relevance) * fatigue)
