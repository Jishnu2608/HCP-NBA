"""Synthetic content library: modular, audience-specific, at mixed MLR approval states.

All titles and bodies are invented placeholders. No real study, product or claim is referenced.
"""

from datetime import date, timedelta

from app.models import Content
from app.models.enums import ActionType, Measure, MlrStatus, TargetType

_LABEL = {
    Measure.DIABETES: ("type 2 diabetes", "diabetes medication"),
    Measure.HYPERTENSION: ("high blood pressure", "blood pressure medication"),
    Measure.CHOLESTEROL: ("high cholesterol", "statin"),
}
_SPECIALIST = {
    Measure.DIABETES: "Endocrinology",
    Measure.HYPERTENSION: "Cardiovascular Disease",
    Measure.CHOLESTEROL: "Cardiovascular Disease",
}

APPROVED, PENDING, REJECTED, EXPIRED = "approved", "pending", "rejected", "expired"
DIGITAL = ["email", "portal"]


def _hcp_specs(m: Measure) -> list[tuple]:
    cond, med = _LABEL[m]
    # (action, subtopic, specialty, channels, state, title, body)
    outcomes_state = EXPIRED if m == Measure.DIABETES else APPROVED
    specs = [
        (ActionType.SHARE_STUDY, "outcomes", None, DIGITAL, outcomes_state,
         f"Adherence and outcomes in {cond}: cohort summary",
         f"Summary of a synthetic cohort analysis linking {med} adherence (PDC of 80% or more) "
         "to fewer acute events. Illustrative content for demonstration only."),
        (ActionType.SHARE_STUDY, "guidelines", None, DIGITAL, APPROVED,
         f"Guideline update digest: {cond}",
         f"Two-page digest of recent guideline changes relevant to managing {cond} in "
         "primary and specialty care. Illustrative content for demonstration only."),
        (ActionType.HCP_EDUCATION, "adherence", None, DIGITAL, APPROVED,
         f"Spotting non-adherence early in {cond}",
         f"Practical signals that a patient is drifting off their {med}, and conversation "
         "openers that work in a short visit. Illustrative content for demonstration only."),
        (ActionType.HCP_EDUCATION, "guidelines", _SPECIALIST[m], ["portal"],
         REJECTED if m == Measure.HYPERTENSION else APPROVED,
         f"Case-based module: complex {cond}",
         f"Interactive specialist cases on treatment intensification in {cond}. "
         "Illustrative content for demonstration only."),
        (ActionType.PROGRAM_INFO, "program", None, ["email", "portal", "rep_visit"],
         PENDING if m == Measure.CHOLESTEROL else APPROVED,
         f"Patient support programme overview: {cond}",
         f"How the support programme helps patients start and stay on their {med}: refill "
         "reminders, education and cost assistance. Illustrative content for demonstration only."),
        (ActionType.REP_VISIT, "adherence", None, ["rep_visit"], APPROVED,
         f"Detail aid: adherence support in {cond}",
         f"In-person discussion guide covering adherence barriers and support options for "
         f"patients on a {med}. Illustrative content for demonstration only."),
    ]  # fmt: skip
    if m == Measure.DIABETES:
        # Refreshed version of the expired outcomes summary, still waiting on MLR.
        specs.append(
            (ActionType.SHARE_STUDY, "outcomes", None, DIGITAL, PENDING,
             f"Adherence and outcomes in {cond}: updated cohort summary",
             "Updated synthetic cohort analysis with two further years of follow-up. "
             "Illustrative content for demonstration only.")
        )  # fmt: skip
    return specs


def education_text(m: Measure, subtopic: str) -> str:
    """The patient education material itself (not a description of it): what a patient
    reads in their message. Plain language, no links, nothing that changes a dose."""
    cond, med = _LABEL[m]
    if subtopic == "adherence":
        return (
            f"Your {med} works quietly in the background: it keeps {cond} under control "
            "only while it is in your body every day, even on days you feel completely well. "
            "Missing doses lets the effect wear off without you noticing.\n\n"
            "Three things that help most people:\n"
            "- Take it at the same time each day, linked to something you already do, such "
            "as brushing your teeth or your evening meal.\n"
            "- Keep a week's supply in a pill organiser where you will see it.\n"
            "- Order your next supply when about a week is left, so you never run out.\n\n"
            "If you miss a dose and are unsure what to do, ask your pharmacist or care team. "
            "If side effects, cost or anything else makes it hard to keep going, tell your "
            "care team: there is usually a way to help."
        )
    return (
        f"Living well with {cond} is mostly about small, steady habits.\n\n"
        "- Food: plenty of vegetables, whole grains and pulses; less salt, sugar and "
        "processed food.\n"
        "- Activity: about 30 minutes of walking or similar on most days, building up "
        "gently.\n"
        "- Check-ups: keep your regular reviews and bring any home readings with you.\n"
        f"- Medicines: keep taking your {med} as prescribed; it works alongside these "
        "habits, not instead of them.\n\n"
        "Your care team can help you choose one change to start with."
    )


def _patient_specs(m: Measure) -> list[tuple]:
    cond, med = _LABEL[m]
    return [
        (ActionType.REFILL_NUDGE, "adherence", ["sms"], APPROVED,
         f"Refill reminder (text): {med}",
         "Hi {{ first_name }}, your {{ drug_name }} refill is due. Reply YES and we will help "
         "you arrange it, or call your care team with any questions."),
        (ActionType.REFILL_NUDGE, "adherence", DIGITAL, APPROVED,
         f"Refill reminder: {med}",
         "Hello {{ first_name }}, our records show your {{ drug_name }} supply has run out or "
         "is about to. Refilling on time keeps your treatment working. You can request a "
         "refill through the portal or ask your care team for help."),
        (ActionType.EDUCATION, "adherence", DIGITAL, APPROVED,
         f"Why taking your {med} every day matters",
         education_text(m, "adherence")),
        (ActionType.EDUCATION, "guidelines", ["portal", "email"],
         PENDING if m == Measure.DIABETES else APPROVED,
         f"Living well with {cond}",
         education_text(m, "guidelines")),
        (ActionType.CHECK_IN, "adherence", ["phone"], APPROVED,
         f"Care-team check-in call guide: {med}",
         "Call guide: ask how {{ first_name }} is getting on with {{ drug_name }}, listen for "
         "side effects, cost or routine barriers, and agree one next step."),
        (ActionType.COST_SUPPORT, "program", ["phone", "email", "portal"], APPROVED,
         f"Help with the cost of your {med}",
         "{{ first_name }}, if cost is making it hard to refill {{ drug_name }}, support may be "
         "available. Your care team can check savings options with you."),
    ]  # fmt: skip


_GENERAL_HCP = [
    (ActionType.HCP_EDUCATION, "general_adherence", DIGITAL, APPROVED,
     "Medication adherence and quality measures: a primer",
     "How proportion of days covered feeds adherence quality measures, and what moves it. "
     "Illustrative content for demonstration only."),
    (ActionType.SHARE_STUDY, "general_adherence", ["email"], APPROVED,
     "What drives non-adherence: evidence summary",
     "Synthetic evidence summary of cost, complexity and forgetfulness as adherence barriers. "
     "Illustrative content for demonstration only."),
    (ActionType.PROGRAM_INFO, "general_program", ["email", "rep_visit"], EXPIRED,
     "Support programme enrolment guide (previous edition)",
     "Superseded enrolment guide. Illustrative content for demonstration only."),
]  # fmt: skip

_GENERAL_PATIENT = [
    (ActionType.REFILL_NUDGE, "general_adherence", ["phone"], APPROVED,
     "Refill reminder call guide",
     "Call guide: remind {{ first_name }} that {{ drug_name }} is due for refill and offer "
     "help arranging it."),
    (ActionType.EDUCATION, "general_adherence", ["sms"], REJECTED,
     "Daily medication tips (text series)",
     "Text series with daily medication tips. Rejected at review: wording needs revision."),
    (ActionType.COST_SUPPORT, "general_program", ["sms"], APPROVED,
     "Cost help available (text)",
     "Hi {{ first_name }}, help with medication costs may be available. Reply HELP and your "
     "care team will call you."),
]  # fmt: skip


_INDICATION = {
    Measure.DIABETES: "Adults with type 2 diabetes on long-term medication",
    Measure.HYPERTENSION: "Adults with high blood pressure on long-term medication",
    Measure.CHOLESTEROL: "Adults with high cholesterol on statin therapy",
}
LIBRARY_SAFETY = (
    "Synthetic demonstration material: makes no product efficacy or safety claim. Patients "
    "follow their prescriber's advice and report side effects to their care team."
)
LIBRARY_LABELLING = "No product labelling applies (non-promotional adherence material)."


def library_governance(content_id: str, title: str, measure: str | None) -> dict:
    """The reviewable facts of a built-in library item (synthetic placeholders)."""
    return {
        "claims": [
            {
                "text": f"{title}: illustrative synthetic claim",
                "reference": f"Synthetic library source {content_id} (demonstration only)",
            }
        ],
        "indication": _INDICATION.get(
            measure, "Adults on long-term medication for a chronic condition"
        ),
        "safety_info": LIBRARY_SAFETY,
        "labelling_note": LIBRARY_LABELLING,
    }


def build_content(as_of: date) -> list[Content]:
    rows: list[Content] = []

    def add(audience, action, topic, measure, specialty, channels, state, title, body):
        n = len(rows) + 1
        # Stagger effective dates deterministically; all approved items predate the history window.
        effective = as_of - timedelta(days=420 + (n * 37) % 280)
        if state == APPROVED:
            status, eff, exp = MlrStatus.APPROVED, effective, effective + timedelta(days=1095)
        elif state == EXPIRED:
            status, eff, exp = MlrStatus.APPROVED, effective, as_of - timedelta(days=30)
        else:
            status, eff, exp = MlrStatus(state), None, None
        content_id = f"CNT_{n:03d}"
        # The updated cohort summary is the second version of the first item of its measure.
        previous = next(
            (r.content_id for r in rows if "updated" in title and r.topic == topic), None
        )
        rows.append(
            Content(
                content_id=content_id,
                version=2 if previous else 1,
                lineage_id=previous or content_id,
                previous_id=previous,
                origin="library",
                **library_governance(content_id, title, measure),
                title=title,
                body=body,
                audience=audience,
                action_type=action,
                topic=topic,
                measure=measure,
                specialty=specialty,
                channels=list(channels),
                mlr_status=status,
                effective_date=eff,
                expiry_date=exp,
            )
        )

    for m in Measure:
        for action, subtopic, specialty, channels, state, title, body in _hcp_specs(m):
            add(
                TargetType.HCP,
                action,
                f"{m}_{subtopic}",
                m,
                specialty,
                channels,
                state,
                title,
                body,
            )
    for action, topic, channels, state, title, body in _GENERAL_HCP:
        add(TargetType.HCP, action, topic, None, None, channels, state, title, body)
    for m in Measure:
        for action, subtopic, channels, state, title, body in _patient_specs(m):
            add(
                TargetType.PATIENT, action, f"{m}_{subtopic}", m, None, channels, state, title, body
            )
    for action, topic, channels, state, title, body in _GENERAL_PATIENT:
        add(TargetType.PATIENT, action, topic, None, None, channels, state, title, body)
    return rows


def valid_on(content: Content, day: date) -> bool:
    """True if the content was MLR-approved and inside its effective window on the given day."""
    return (
        content.mlr_status == MlrStatus.APPROVED
        and content.effective_date is not None
        and content.effective_date <= day
        and (content.expiry_date is None or day < content.expiry_date)
    )
