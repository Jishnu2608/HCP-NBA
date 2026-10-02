"""Engagement features built from interaction history.

One running tally per target is walked forward in time. The same code produces
point-in-time training rows (features as they stood just before each past touch) and
today's features for scoring, so training and inference cannot drift apart.
"""

from collections import Counter
from datetime import date
from math import log1p

from app.features.adherence import Adherence, compute_adherence
from app.features.population import Population
from app.models import Content, Hcp, Patient, PatientTherapy
from app.models.enums import ActionType, Measure, Outcome, TargetType

QUIET = frozenset({Outcome.NO_RESPONSE, Outcome.DECLINED, Outcome.PENDING})
PRIOR_STRENGTH = 3.0
PATIENT_ENGAGE_PRIOR = 0.35
PATIENT_FILL_PRIOR = 0.12
HCP_ENGAGE_PRIOR = 0.25
NO_HISTORY_DAYS = 180

SPECIALIST_FOR = {
    Measure.DIABETES: "Endocrinology",
    Measure.HYPERTENSION: "Cardiovascular Disease",
    Measure.CHOLESTEROL: "Cardiovascular Disease",
}
PRIMARY_CARE = ("Family Medicine", "Internal Medicine")


def is_engaged(outcome: str) -> bool:
    return outcome not in QUIET


class EngagementState:
    """Running tally of one target's outreach history up to a moment in time."""

    def __init__(self) -> None:
        self.sent: Counter = Counter()
        self.engaged: Counter = Counter()
        self.filled: Counter = Counter()
        self.days: list[date] = []
        self.outcomes: list[bool] = []

    def record(self, keys: list[tuple], day: date, engaged: bool, filled: bool = False) -> None:
        for key in [("all",), *keys]:
            self.sent[key] += 1
            self.engaged[key] += engaged
            self.filled[key] += filled
        self.days.append(day)
        self.outcomes.append(engaged)

    def touch(self, day: date) -> None:
        """A contact whose outcome is not known yet: counts for frequency, not for rates."""
        self.days.append(day)

    def rate(self, key: tuple, prior: float) -> float:
        """Engagement rate shrunk toward a prior, so one lucky response is not over-read."""
        return (self.engaged[key] + prior * PRIOR_STRENGTH) / (self.sent[key] + PRIOR_STRENGTH)

    def fill_rate(self, key: tuple, prior: float) -> float:
        return (self.filled[key] + prior * PRIOR_STRENGTH) / (self.sent[key] + PRIOR_STRENGTH)

    def recent(self, day: date, window: int) -> int:
        return sum(1 for d in self.days if 0 <= (day - d).days <= window)

    def days_since_last(self, day: date) -> int:
        return min(NO_HISTORY_DAYS, (day - self.days[-1]).days) if self.days else NO_HISTORY_DAYS

    def unresponsive_share(self, last_n: int = 3) -> float:
        tail = self.outcomes[-last_n:]
        return sum(1 for engaged in tail if not engaged) / len(tail) if tail else 0.0


# --- Patient -----------------------------------------------------------------

PATIENT_NUMERIC = [
    "age", "copay", "days_supply", "gap_days", "pdc", "late_trend", "mean_lateness", "n_fills",
    "never_filled", "rate_channel", "n_channel", "rate_all", "fill_rate_action", "fill_rate_all",
    "recent_14", "days_since_last", "is_preferred_channel",
    # Action-specific effects a linear model cannot otherwise express.
    "copay_if_cost_support", "never_filled_if_education", "late_trend_if_check_in",
]  # fmt: skip
PATIENT_CATEGORICAL = ["channel", "action", "plan_type", "measure"]


def patient_features(
    patient: Patient,
    therapy: PatientTherapy,
    adherence: Adherence,
    state: EngagementState,
    day: date,
    channel: str,
    action: str,
) -> dict:
    return {
        "age": round((day - patient.birth_date).days / 365.25, 1),
        "copay": therapy.copay,
        "days_supply": therapy.days_supply,
        "gap_days": min(adherence.gap_days, 90),
        "pdc": adherence.pdc,
        "late_trend": adherence.late_trend,
        "mean_lateness": adherence.mean_lateness,
        "n_fills": adherence.n_fills,
        "never_filled": int(adherence.never_filled),
        "rate_channel": state.rate(("channel", channel), PATIENT_ENGAGE_PRIOR),
        "n_channel": state.sent[("channel", channel)],
        "rate_all": state.rate(("all",), PATIENT_ENGAGE_PRIOR),
        "fill_rate_action": state.fill_rate(("action", action), PATIENT_FILL_PRIOR),
        "fill_rate_all": state.fill_rate(("all",), PATIENT_FILL_PRIOR),
        "recent_14": state.recent(day, 14),
        "days_since_last": state.days_since_last(day),
        "is_preferred_channel": int(patient.preferred_channel == channel),
        "copay_if_cost_support": therapy.copay if action == ActionType.COST_SUPPORT else 0.0,
        "never_filled_if_education": int(adherence.never_filled and action == ActionType.EDUCATION),
        "late_trend_if_check_in": adherence.late_trend if action == ActionType.CHECK_IN else 0.0,
        "channel": str(channel),
        "action": str(action),
        "plan_type": patient.plan_type,
        "measure": str(therapy.measure),
    }


def _record_patient(state: EngagementState, interaction) -> None:
    if interaction.outcome == Outcome.PENDING:
        return state.touch(interaction.int_ts.date())
    state.record(
        [("channel", interaction.channel), ("action", interaction.type)],
        interaction.int_ts.date(),
        is_engaged(interaction.outcome),
        interaction.outcome == Outcome.FILLED,
    )


def patient_state(pop: Population, patient_id: str) -> EngagementState:
    state = EngagementState()
    for i in pop.interactions.get((TargetType.PATIENT, patient_id), []):
        _record_patient(state, i)
    return state


def patient_training_rows(pop: Population, window_days: int) -> list[dict]:
    """One row per past patient touch, with features as of the moment it was sent."""
    rows = []
    for pid, patient in pop.patients.items():
        therapies = {t.id: t for t in pop.therapies.get(pid, [])}
        state = EngagementState()
        for i in pop.interactions.get((TargetType.PATIENT, pid), []):
            therapy = therapies.get(i.therapy_id)
            if therapy and i.outcome != Outcome.PENDING:
                day = i.int_ts.date()
                adherence = compute_adherence(
                    pop.fills.get(therapy.id, []), therapy.start_date, day, window_days
                )
                row = patient_features(patient, therapy, adherence, state, day, i.channel, i.type)
                row.update(
                    target_id=pid,
                    source=i.source,
                    ts=i.int_ts,
                    engaged=int(is_engaged(i.outcome)),
                    filled=int(i.outcome == Outcome.FILLED),
                )
                rows.append(row)
            _record_patient(state, i)
    return rows


# --- HCP ---------------------------------------------------------------------

HCP_NUMERIC = [
    "rx_volume_log", "rate_channel", "n_channel", "rate_all", "rate_measure", "rate_subtopic",
    "recent_30", "days_since_last", "specialty_match",
]  # fmt: skip
HCP_CATEGORICAL = ["channel", "action", "specialty", "measure", "subtopic"]


def topic_parts(content: Content) -> tuple[str, str]:
    """(measure or 'general', subtopic) for a content item."""
    return str(content.measure or "general"), content.topic.rpartition("_")[2]


def specialty_match(specialty: str, measure: str | None) -> float:
    if measure is None or measure == "general":
        return 0.5
    if SPECIALIST_FOR.get(Measure(measure)) == specialty:
        return 1.0
    return 0.6 if specialty in PRIMARY_CARE else 0.2


def hcp_features(
    hcp: Hcp, state: EngagementState, day: date, channel: str, content: Content
) -> dict:
    measure, subtopic = topic_parts(content)
    return {
        "rx_volume_log": round(log1p(hcp.rx_volume_annual), 3),
        "rate_channel": state.rate(("channel", channel), HCP_ENGAGE_PRIOR),
        "n_channel": state.sent[("channel", channel)],
        "rate_all": state.rate(("all",), HCP_ENGAGE_PRIOR),
        "rate_measure": state.rate(("measure", measure), HCP_ENGAGE_PRIOR),
        "rate_subtopic": state.rate(("subtopic", subtopic), HCP_ENGAGE_PRIOR),
        "recent_30": state.recent(day, 30),
        "days_since_last": state.days_since_last(day),
        "specialty_match": specialty_match(hcp.specialty, measure),
        "channel": str(channel),
        "action": str(content.action_type),
        "specialty": hcp.specialty,
        "measure": measure,
        "subtopic": subtopic,
    }


def _record_hcp(state: EngagementState, interaction, content: Content | None) -> None:
    if interaction.outcome == Outcome.PENDING:
        return state.touch(interaction.int_ts.date())
    keys = [("channel", interaction.channel)]
    if content:
        measure, subtopic = topic_parts(content)
        keys += [("measure", measure), ("subtopic", subtopic)]
    state.record(keys, interaction.int_ts.date(), is_engaged(interaction.outcome))


def hcp_state(pop: Population, hcp_id: str) -> EngagementState:
    state = EngagementState()
    for i in pop.interactions.get((TargetType.HCP, hcp_id), []):
        _record_hcp(state, i, pop.contents.get(i.content_id))
    return state


def hcp_training_rows(pop: Population) -> list[dict]:
    """One row per past HCP touch, with features as of the moment it was sent."""
    rows = []
    for hcp_id, hcp in pop.hcps.items():
        state = EngagementState()
        for i in pop.interactions.get((TargetType.HCP, hcp_id), []):
            content = pop.contents.get(i.content_id)
            if content and i.outcome != Outcome.PENDING:
                row = hcp_features(hcp, state, i.int_ts.date(), i.channel, content)
                row.update(
                    target_id=hcp_id,
                    source=i.source,
                    ts=i.int_ts,
                    engaged=int(is_engaged(i.outcome)),
                )
                rows.append(row)
            _record_hcp(state, i, content)
    return rows
