"""Deterministic safety gate between a model answer and a reader.

The model's text is shown only if every check passes. Otherwise the reader gets the template
brief. Either way the patient's identity comes from the case (the authorized record), never from
model text. This is the shape the real AI layer will follow; here it runs only in the evaluation.
"""

from pydantic import ValidationError

from .baseline import template_brief
from .cases import Case
from .checks import evaluate
from .schema import CaseBrief


def decide(case: Case, text: str | None, show_patient_id: bool = False) -> dict:
    """Return what a reader would see: {patient_id, source, brief, reasons}."""
    reasons: list[str] = []
    brief = None
    if text is None:
        reasons.append("no_answer")
    else:
        try:
            brief = CaseBrief.model_validate_json(text)
        except (ValidationError, ValueError):
            reasons.append("schema")
    if brief is not None:
        result = evaluate(case, brief, show_patient_id)
        reasons += sorted({c["kind"].split(":")[0] for c in result["critical"]})
        if result["miscited_statements"]:
            reasons.append("miscited")
    shown = template_brief(case) if reasons else brief
    return {
        "patient_id": case.patient_id,
        "source": "template" if reasons else "model",
        "brief": shown.model_dump(),
        "reasons": reasons,
    }
