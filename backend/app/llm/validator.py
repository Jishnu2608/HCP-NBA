"""Checks model output before it is stored. Anything that fails is discarded, never repaired.

The model is treated as an untrusted writer: valid structure, inside channel limits, and
no reference to content other than the approved module it was given.
"""

import json
import re

from pydantic import ValidationError

from app.llm.base import DraftOutput, DraftRequest
from app.models.enums import Channel, TargetType

SMS_MAX_CHARS = 320
BODY_MAX_CHARS = 2000
SUMMARY_MAX_CHARS = 600
CONTENT_ID = re.compile(r"\bCNT_\d{3}\b")
URL = re.compile(r"https?://|www\.", re.IGNORECASE)
# Wording a patient-facing adherence message must never contain.
BANNED_PATIENT_PHRASES = (
    "cure",
    "guarantee",
    "stop taking",
    "double dose",
    "double your dose",
    "skip your dose",
    "no side effects",
)
NEEDS_SUBJECT = (Channel.EMAIL, Channel.PORTAL)


class DraftValidationError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def parse(raw: str | dict | DraftOutput) -> DraftOutput:
    if isinstance(raw, DraftOutput):
        return raw
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return DraftOutput.model_validate(data)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise DraftValidationError([f"malformed output: {type(exc).__name__}"]) from exc


def validate(raw: str | dict | DraftOutput, request: DraftRequest) -> DraftOutput:
    """Returns the parsed output, or raises DraftValidationError listing every problem."""
    output = parse(raw)
    problems = []
    if not output.rationale_summary.strip():
        problems.append("empty rationale summary")
    if len(output.rationale_summary) > SUMMARY_MAX_CHARS:
        problems.append("rationale summary too long")

    for n, variant in enumerate(output.variants, start=1):
        text = f"{variant.subject or ''}\n{variant.body}"
        if not variant.body.strip():
            problems.append(f"variant {n}: empty body")
        if "{{" in text or "}}" in text:
            problems.append(f"variant {n}: unresolved placeholder")
        if URL.search(text):
            problems.append(f"variant {n}: contains a link that is not in the approved content")
        foreign = set(CONTENT_ID.findall(text)) - {request.content_id}
        if foreign:
            problems.append(f"variant {n}: references other content {sorted(foreign)}")
        if request.channel == Channel.SMS:
            if variant.subject:
                problems.append(f"variant {n}: text message cannot have a subject")
            if len(variant.body) > SMS_MAX_CHARS:
                problems.append(f"variant {n}: text message over {SMS_MAX_CHARS} characters")
        elif len(variant.body) > BODY_MAX_CHARS:
            problems.append(f"variant {n}: body over {BODY_MAX_CHARS} characters")
        if request.channel in NEEDS_SUBJECT and not (variant.subject or "").strip():
            problems.append(f"variant {n}: subject required for {request.channel}")
        if request.target_type == TargetType.PATIENT:
            for phrase in BANNED_PATIENT_PHRASES:
                if re.search(rf"\b{re.escape(phrase)}", text, re.IGNORECASE):
                    problems.append(f"variant {n}: disallowed wording '{phrase}'")

    if problems:
        raise DraftValidationError(problems)
    return output
