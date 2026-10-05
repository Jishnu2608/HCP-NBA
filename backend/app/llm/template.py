"""Offline provider: assembles messages from the approved content module and fixed phrases.

Needs no key and no network, so the demo always works, and it is the fallback whenever a
real model's output fails validation.
"""

from app.llm.base import DraftOutput, DraftRequest, DraftResult, MessageVariant
from app.models.enums import ActionType, Channel, TargetType
from app.nba.rationale import ACTION_LABEL, CHANNEL_LABEL

_PATIENT_SUBJECT = {
    ActionType.REFILL_NUDGE: "Your {drug} refill",
    ActionType.EDUCATION: "Getting the most from your {drug}",
    ActionType.COST_SUPPORT: "Help with the cost of your {drug}",
    ActionType.CHECK_IN: "Checking in about your {drug}",
}
_PATIENT_SHORT = {
    ActionType.REFILL_NUDGE: "Hi {first}, a reminder from your care team: your {drug} refill "
    "is due. Reply YES if you would like help arranging it.",
    ActionType.EDUCATION: "Hi {first}, your care team has shared a short guide about your "
    "{drug} in your portal. Reply HELP if you have questions.",
    ActionType.COST_SUPPORT: "Hi {first}, help with the cost of your {drug} may be available. "
    "Reply HELP and your care team will call you.",
    ActionType.CHECK_IN: "Hi {first}, your care team would like to check in about your {drug}. "
    "Reply CALL to choose a time.",
}


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def render(text: str, request: DraftRequest) -> str:
    """Fills the only two placeholders approved content may contain."""
    return text.replace("{{ first_name }}", request.first_name).replace(
        "{{ drug_name }}", request.drug_name or "medication"
    )


def summarize(request: DraftRequest) -> str:
    lead = request.reasons[0] if request.reasons else "Outreach recommended"
    action = ACTION_LABEL.get(request.action, request.action)
    channel = CHANNEL_LABEL.get(request.channel, request.channel)
    return f"{lead}. Recommended next step: {action} by {channel}."


def _patient_variants(r: DraftRequest) -> list[MessageVariant]:
    body = render(r.content_body, r)
    drug = r.drug_name or "medication"
    short = _PATIENT_SHORT[r.action].format(first=r.first_name, drug=drug)
    if r.channel == Channel.SMS:
        return [MessageVariant(body=body), MessageVariant(body=short)]
    if r.channel == Channel.PHONE:
        return [
            MessageVariant(
                subject=f"Call guide: {r.first_name} {r.last_name}",
                body=(
                    f"1. Open: confirm you are speaking with {r.first_name} and ask if now is "
                    "a good time.\n"
                    f"2. Purpose: {body}\n"
                    "3. Listen: ask what has made it hard lately (cost, side effects, routine) "
                    "and note the answer.\n"
                    "4. Close: agree one next step and when you will follow up."
                ),
            ),
            MessageVariant(
                subject=f"Voicemail: {r.first_name} {r.last_name}",
                body=(
                    f"Hello {r.first_name}, this is your care team calling about your {drug}. "
                    "Please call us back when convenient. No medical details will be left on "
                    "this message."
                ),
            ),
        ]
    subject = _PATIENT_SUBJECT[r.action].format(drug=drug)
    lead = _sentence(short.split(", ", 1)[-1])
    if r.action == ActionType.EDUCATION:
        # The guide itself travels with the message; a pointer to it alone is not enough.
        lead = f"Here is a short guide from your care team about your {drug}.\n\n{body}"
    return [
        MessageVariant(subject=subject, body=f"Hello {r.first_name},\n\n{body}\n\nYour care team"),
        MessageVariant(
            subject=f"{r.first_name}, a note from your care team",
            body=f"Hello {r.first_name},\n\n{lead}\n\nYour care team",
        ),
    ]


def _hcp_variants(r: DraftRequest) -> list[MessageVariant]:
    body = render(r.content_body, r)
    relevance = f"your {r.specialty} practice" if r.specialty else "your practice"
    if r.channel == Channel.REP_VISIT:
        why = r.reasons[1] if len(r.reasons) > 1 else r.reasons[0]
        return [
            MessageVariant(
                subject=f"Visit brief: Dr. {r.last_name}",
                body=(
                    f"1. Why now: {why}.\n"
                    f"2. Lead with: {r.content_title} ({r.content_id}).\n"
                    f"3. Key point: {body}\n"
                    "4. Ask: which adherence barriers come up most with their patients.\n"
                    "5. Stay within the approved material; log the outcome after the visit."
                ),
            ),
            MessageVariant(
                subject=f"Short brief: Dr. {r.last_name}",
                body=f"Lead with {r.content_title} ({r.content_id}). {body}",
            ),
        ]
    return [
        MessageVariant(
            subject=r.content_title,
            body=(
                f"Dear Dr. {r.last_name},\n\nThis may be useful for {relevance}.\n\n"
                f"{body}\n\nKind regards,\nMedical Engagement Team"
            ),
        ),
        MessageVariant(
            subject=f"For {relevance}: {r.content_title}",
            body=(
                f"Dear Dr. {r.last_name},\n\n{body}\n\n"
                "Happy to arrange a short discussion if helpful.\n\nKind regards,\n"
                "Medical Engagement Team"
            ),
        ),
    ]


class TemplateProvider:
    name = "template"

    def draft(self, request: DraftRequest) -> DraftResult:
        variants = (
            _patient_variants(request)
            if request.target_type == TargetType.PATIENT
            else _hcp_variants(request)
        )
        return DraftResult(
            output=DraftOutput(rationale_summary=summarize(request), variants=variants),
            provider=self.name,
        )
