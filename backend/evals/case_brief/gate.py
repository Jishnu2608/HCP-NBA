"""Deterministic safety gate between a model answer and a reader.

The model's text is shown only if every check passes. Otherwise the reader gets the template
brief. Either way the patient's identity comes from the case (the authorized record), never from
model text. This is the shape the real AI layer will follow; here it runs only in the evaluation.
"""

from pydantic import ValidationError

from . import hybrid
from .baseline import template_brief
from .cases import Case
from .checks import evaluate
from .prompt import view_for
from .schema import CaseBrief


def assess(case: Case, text: str | None, version: int) -> tuple[dict | None, dict]:
    """(evaluation or None, decision) for one stored or fresh answer of a prompt version."""
    view = view_for(version)
    reasons: list[str] = []
    brief = evaluation = None
    if text is None:
        reasons.append("no_answer")
    else:
        try:
            if version >= 5:
                narrative = hybrid.ModelNarrative.model_validate_json(text)
                brief = hybrid.compose(case, narrative)
                extra, miscited = hybrid.check_citations(case, narrative, view)
            else:
                brief, extra, miscited = CaseBrief.model_validate_json(text), [], 0
        except (ValidationError, ValueError):
            reasons.append("schema")
    if brief is not None:
        evaluation = evaluate(case, brief, **view)
        evaluation["critical"] += extra
        evaluation["miscited_statements"] += miscited
        evaluation["statements"] += 2 if version >= 5 else 0
        reasons += sorted({c["kind"].split(":")[0] for c in evaluation["critical"]})
        if evaluation["miscited_statements"]:
            reasons.append("miscited")
    shown = template_brief(case) if reasons else brief
    decision = {
        "patient_id": case.patient_id,
        "source": "template" if reasons else "model",
        "brief": shown.model_dump(),
        "reasons": reasons,
    }
    return evaluation, decision


def decide(case: Case, text: str | None, show_patient_id: bool = False) -> dict:
    """A full-brief answer (prompt v3/v4 shape): what a reader would see."""
    return assess(case, text, 2 if show_patient_id else 4)[1]
