"""Hard eligibility gates: MLR approval, consent and contact frequency.

Deterministic, side-effect free and independent of any model or LLM. The engine calls
these when it builds recommendations, and the send path calls them again immediately
before delivery, so an approval that lapsed or a consent that was withdrawn in between
still stops the message.
"""

from datetime import date

from app.features.engagement import EngagementState
from app.models import Consent, Content
from app.models.enums import ConsentPurpose, MlrStatus, TargetType

MLR_PREFIX = "mlr_"
CONSENT_MISSING = "consent_missing"
FREQUENCY_CODES = frozenset({"frequency_cap", "min_gap"})

GATE_TEXT = {
    "audience_mismatch": "Content is not intended for this audience",
    "channel_not_allowed": "Content is not cleared for this channel",
    "mlr_pending": "Content is still pending MLR review",
    "mlr_rejected": "Content was rejected at MLR review",
    "mlr_not_effective": "Content approval is not yet effective",
    "mlr_expired": "Content approval has expired",
    CONSENT_MISSING: "Patient has not consented to outreach on this channel",
    "frequency_cap": "Contact frequency cap reached",
    "min_gap": "Too soon after the previous contact",
}


def content_failures(content: Content, audience: str, channel: str, day: date) -> list[str]:
    failures = []
    if content.audience != audience:
        failures.append("audience_mismatch")
    if channel not in content.channels:
        failures.append("channel_not_allowed")
    if content.mlr_status != MlrStatus.APPROVED:
        failures.append(f"{MLR_PREFIX}{content.mlr_status}")
    elif content.effective_date is None or content.effective_date > day:
        failures.append("mlr_not_effective")
    elif content.expiry_date is not None and content.expiry_date <= day:
        failures.append("mlr_expired")
    return failures


def has_consent(consents: list[Consent], channel: str, day: date) -> bool:
    """True only if a granted outreach consent for this channel is in effect on the day."""
    return any(
        c.granted
        and c.purpose == ConsentPurpose.OUTREACH
        and c.channel == channel
        and c.effective_from <= day
        and (c.effective_to is None or day < c.effective_to)
        for c in consents
    )


def frequency_failures(state: EngagementState, day: date, cap: dict) -> list[str]:
    failures = []
    if state.recent(day, cap["window_days"] - 1) >= cap["max_touches"]:
        failures.append("frequency_cap")
    if state.days and (day - state.days[-1]).days < cap["min_gap_days"]:
        failures.append("min_gap")
    return failures


def evaluate(
    target_type: str,
    content: Content,
    channel: str,
    day: date,
    consents: list[Consent],
    state: EngagementState,
    caps: dict,
) -> list[str]:
    """All gate failures for one proposed touch. Empty list means it may proceed."""
    failures = content_failures(content, target_type, channel, day)
    if target_type == TargetType.PATIENT and not has_consent(consents, channel, day):
        failures.append(CONSENT_MISSING)
    failures += frequency_failures(state, day, caps[target_type])
    return failures


def is_compliance(code: str) -> bool:
    return code.startswith(MLR_PREFIX) or code in ("audience_mismatch", "channel_not_allowed")


def compliance_ok(failures: list[str]) -> bool:
    return not any(is_compliance(f) for f in failures)


def consent_ok(failures: list[str]) -> bool:
    return CONSENT_MISSING not in failures


def frequency_limited(failures: list[str]) -> bool:
    return any(f in FREQUENCY_CODES for f in failures)


def describe(failures: list[str]) -> str:
    return "; ".join(GATE_TEXT.get(f, f) for f in failures)
