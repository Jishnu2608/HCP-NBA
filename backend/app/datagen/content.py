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
         f"A short, plain-language guide to how your {med} works for {cond} and simple "
         "routines that make daily doses easier to remember."),
        (ActionType.EDUCATION, "guidelines", ["portal", "email"],
         PENDING if m == Measure.DIABETES else APPROVED,
         f"Living well with {cond}",
         f"Everyday tips on food, activity and check-ups for people managing {cond}."),
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
        rows.append(
            Content(
                content_id=f"CNT_{n:03d}",
                version=2 if "updated" in title else 1,
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
