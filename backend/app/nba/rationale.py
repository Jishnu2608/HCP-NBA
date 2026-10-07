"""Builds the plain-language rationale from facts the engine already computed.

Every sentence is traceable to a number in the data: a risk driver, a response count, a
gate result. The LLM layer may rephrase this text later; it never adds reasons of its own.
"""

from datetime import date

from app.clinical.vocabulary import specialty_label
from app.features.adherence import Adherence
from app.features.engagement import EngagementState
from app.models import Content, Hcp
from app.models.enums import ActionType, Channel
from app.nba import gates
from app.scoring.risk import Risk

ACTION_LABEL = {
    ActionType.REFILL_NUDGE: "refill reminder",
    ActionType.CHECK_IN: "care-team check-in",
    ActionType.EDUCATION: "education message",
    ActionType.COST_SUPPORT: "cost-support offer",
    ActionType.SHARE_STUDY: "study summary",
    ActionType.HCP_EDUCATION: "educational content",
    ActionType.REP_VISIT: "rep visit",
    ActionType.PROGRAM_INFO: "support-programme information",
}
CHANNEL_LABEL = {
    Channel.EMAIL: "email",
    Channel.SMS: "text message",
    Channel.PORTAL: "portal message",
    Channel.PHONE: "phone call",
    Channel.REP_VISIT: "in-person visit",
}
SEGMENT_LABEL = {
    "high_value_engaged": "high-volume prescriber who engages regularly",
    "high_value_dormant": "high-volume prescriber who rarely engages",
    "medium_value_engaged": "mid-volume prescriber who engages regularly",
    "medium_value_dormant": "mid-volume prescriber who rarely engages",
    "low_value_engaged": "lower-volume prescriber who engages regularly",
    "low_value_dormant": "lower-volume prescriber who rarely engages",
    # Invited HCPs: prescribing volume not recorded.
    "unrated_engaged": "prescribing volume not recorded, engages regularly",
    "unrated_dormant": "prescribing volume not recorded, rarely engages",
    "unrated_new": "prescribing volume not recorded, no engagement history yet",
}


def _specialty_words(hcp) -> str:
    """'Endocrinology physician', or a plain statement when no specialty is configured."""
    if not hcp.specialty:
        return "Physician (specialty not configured)"
    return f"{specialty_label(hcp.specialty)} physician"


def _a(noun: str) -> str:
    return f"{'an' if noun[0] in 'aeiou' else 'a'} {noun}"


def _reason(kind: str, code: str, text: str, **extra) -> dict:
    return {"kind": kind, "code": code, "text": text, **extra}


def _channel_reason(state: EngagementState, channel: str, preferred: str | None) -> dict:
    label = CHANNEL_LABEL[channel]
    sent, engaged = state.sent[("channel", channel)], state.engaged[("channel", channel)]
    if sent:
        text = f"Responded to {engaged} of {sent} earlier contacts by {label}"
        code = "channel_history"
    else:
        text = f"No earlier contact by {label}; chosen on predicted response"
        code = "channel_predicted"
    if preferred == channel:
        text += ", and it is the stated preferred channel"
    return _reason("channel", code, text)


def _withheld_reason(withheld: dict | None) -> list[dict]:
    if not withheld:
        return []
    if withheld.get("kind") == "preferred_channel":
        return [
            _reason(
                "withheld",
                withheld["failures"][0],
                f"Stated preferred channel ({CHANNEL_LABEL[withheld['channel']]}) was not used: "
                f"{gates.describe(withheld['failures'])}",
            )
        ]
    what = withheld.get("title") or CHANNEL_LABEL.get(withheld["channel"], withheld["channel"])
    return [
        _reason(
            "withheld",
            withheld["failures"][0],
            f"A higher-scoring option ({what}) was held back: "
            f"{gates.describe(withheld['failures'])}",
        )
    ]


def _compliance_reason(content: Content, patient_side: bool, channel: str) -> list[dict]:
    reasons = [
        _reason(
            "compliance",
            "mlr_approved",
            f"Content {content.content_id} is MLR-approved until {content.expiry_date:%d %b %Y}",
        )
    ]
    if patient_side:
        reasons.append(
            _reason(
                "compliance",
                "consent_verified",
                f"Patient consent for {CHANNEL_LABEL[channel]} outreach is on record",
            )
        )
    return reasons


def patient_reasons(
    *,
    drug: str,
    adherence: Adherence,
    risk: Risk,
    state: EngagementState,
    action: str,
    channel: str,
    preferred_channel: str | None,
    copay: float,
    content: Content,
    predicted: dict,
    timing_note: str,
    withheld: dict | None,
    eligible: bool,
) -> list[dict]:
    reasons = [_reason("who", d["code"], d["detail"], points=d["points"]) for d in risk.drivers[:3]]
    if not reasons:
        reasons.append(_reason("who", "refill_due", f"{drug} refill is coming due"))

    label = ACTION_LABEL[action]
    fills, sent = state.filled[("action", action)], state.sent[("action", action)]
    if action == ActionType.COST_SUPPORT and copay >= 20:
        text = f"Copay of ${copay:.0f} per fill points to cost as the likely barrier"
        reasons.append(_reason("action", "cost_barrier", text))
    elif sent and fills:
        text = f"Refilled after {fills} of {sent} earlier {label} contacts"
        reasons.append(_reason("action", "action_history", text))
    elif adherence.never_filled:
        text = f"No fill on record yet, so {_a(label)} fits better than a reminder"
        reasons.append(_reason("action", "primary_non_adherence", text))
    else:
        text = f"{_a(label).capitalize()} has the highest predicted chance of a fill"
        reasons.append(_reason("action", "action_predicted", text))
    reasons.append(
        _reason(
            "action",
            "model_estimate",
            f"Model estimate: {predicted['p_outcome']:.0%} chance of a fill within days, "
            f"{predicted['p_engage']:.0%} chance of a response",
        )
    )
    reasons.append(_channel_reason(state, channel, preferred_channel))
    reasons.append(_reason("timing", "timing", timing_note))
    if eligible:
        reasons += _compliance_reason(content, True, channel)
    return reasons + _withheld_reason(withheld)


def hcp_reasons(
    *,
    hcp: Hcp,
    state: EngagementState,
    channel: str,
    content: Content,
    measure: str,
    subtopic: str,
    predicted: dict,
    timing_note: str,
    withheld: dict | None,
    eligible: bool,
) -> list[dict]:
    reasons = [
        _reason(
            "who",
            "segment",
            f"{_specialty_words(hcp)}, {SEGMENT_LABEL.get(hcp.segment, hcp.segment)} "
            + (
                f"(value score {hcp.value_score:.0f} of 100)"
                if hcp.value_score is not None
                else "(value not rated)"
            ),
        )
    ]
    key = ("measure", measure)
    if state.sent[key] and state.engaged[key]:
        text = (
            f"Engaged with {state.engaged[key]} of {state.sent[key]} earlier items on "
            f"{measure.replace('general', 'general adherence')}"
        )
        reasons.append(_reason("action", "topic_history", text))
    else:
        text = (
            f"Topic matches the {specialty_label(hcp.specialty)} specialty"
            if hcp.specialty
            else "General content, open to every specialty"
        )
        reasons.append(_reason("action", "specialty_match", text))
    key = ("subtopic", subtopic)
    if state.sent[key] and state.engaged[key]:
        text = f"Engaged with {state.engaged[key]} of {state.sent[key]} earlier {subtopic} items"
        reasons.append(_reason("action", "subtopic_history", text))
    reasons.append(
        _reason(
            "action",
            "model_estimate",
            f"Model estimate: {predicted['p_engage']:.0%} chance of engagement",
        )
    )
    reasons.append(_channel_reason(state, channel, None))
    reasons.append(_reason("timing", "timing", timing_note))
    if eligible:
        reasons += _compliance_reason(content, False, channel)
    return reasons + _withheld_reason(withheld)


def to_text(reasons: list[dict]) -> str:
    """One readable paragraph, ordered: who, action, channel, timing, compliance, withheld."""
    return ". ".join(r["text"] for r in reasons) + "."


def patient_timing(adherence: Adherence, today: date, runout: date | None) -> tuple[date, str]:
    if adherence.never_filled:
        return today, f"Act now: started {adherence.gap_days} days ago with no fill on record"
    if adherence.gap_days > 0:
        return today, f"Act now: supply ran out {adherence.gap_days} days ago"
    when = max(today, runout) if runout else today
    return today, f"Act now: supply runs out on {when:%d %b}"


def hcp_timing(state: EngagementState, today: date) -> tuple[date, str]:
    if not state.days:
        return today, "Send now: no contact on record"
    return today, f"Send now: {state.days_since_last(today)} days since the last contact"
