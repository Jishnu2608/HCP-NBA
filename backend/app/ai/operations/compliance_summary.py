"""R2, compliance evidence summary: recorded audit activity, aggregated, for a compliance reviewer
(permission `ai:compliance_summary`; the audit read service decides access).

Only counts of actions per day over a fixed window reach the model: no person, patient, record
id, note or payload (the `audit_activity` source carries none). The model describes activity;
it never calls anything a violation or misconduct, attributes it to anyone or infers intent
(`inferred_misconduct`), and repeats counts exactly as items state them (`derived_number`).
"""

import re

from pydantic import BaseModel, ConfigDict, Field

from app.ai.contracts import AuthorizedContext, GateFinding
from app.ai.operations.common import (
    COMMON_RULES,
    Part,
    build_spec,
    ids,
    items,
    literal_numbers,
    pattern_finding,
    statement,
)
from app.core.permissions import Permission

ID = "compliance_summary"
VERSION = "1"
PROMPT_VERSION = "2"  # 2: evidence ids only in the citation lists
KINDS = ("audit_event",)
PARTS = (Part("summary", "summary_citations", "Audit activity"),)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    summary_citations: list[str] = Field(min_length=1, max_length=16)


SYSTEM = (
    "You summarise recorded audit activity for a compliance reviewer: counts of actions per "
    "day over a fixed window.\n"
    "- summary: two or three sentences on which actions occurred and on which days, repeating "
    "counts exactly as the items state them, and naming the window.\n"
    "- Describe activity only. Never call anything a violation, breach, misconduct or failure, "
    "never attribute activity to a person, and never infer intent or wrongdoing.\n"
    "- Under 80 words.\n" + COMMON_RULES
)

MISCONDUCT = re.compile(
    r"\b(?:violat\w*|breach\w*|misconduct|non-?complian\w*|fraud\w*|wrongdoing|negligen\w*"
    r"|abus\w*|suspicious|illegal|unlawful|culprit|blame\w*|at fault|intentional\w*"
    r"|deliberate\w*|investigat\w*|disciplin\w*)\b",
    re.I,
)


def template(ctx: AuthorizedContext):
    events = items(ctx, "audit_event")
    totals = ctx.facts.get("totals") or {}
    days = ctx.facts.get("window_days")
    if not totals:
        return [statement(f"No audit activity is recorded in the last {days} days.",
                          ids(events))]  # fmt: skip
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    listed = ", ".join(f"{action.replace('_', ' ')} {n}" for action, n in ranked[:6])
    scope = "by action" if len(ranked) <= 6 else f"most frequent 6 of {len(ranked)} actions"
    return [statement(f"In the last {days} days the audit log recorded, {scope}: {listed}.",
                      ids(events))]  # fmt: skip


def key_points(ctx: AuthorizedContext):
    return [statement(i.text, [i.id], "record") for i in items(ctx, "audit_event")][-16:]


def notes(ctx: AuthorizedContext):
    return [
        "Counts per action and day only: no person, patient or record is identified and no "
        "note or payload is included.",
        "Activity counts are not findings: whether anything needs follow-up is a reviewer's "
        "decision.",
    ]


def check(ctx: AuthorizedContext, out: BaseModel) -> tuple[list[GateFinding], int]:
    findings = pattern_finding(MISCONDUCT, "inferred_misconduct", out.summary, "summary")
    findings += literal_numbers(ctx, out.summary, "summary")
    return findings, 0


SPEC = build_spec(
    id_=ID,
    version=VERSION,
    prompt_version=PROMPT_VERSION,
    permission=Permission.AI_COMPLIANCE_SUMMARY,
    subject_type="audit_activity",
    kinds=KINDS,
    system=SYSTEM,
    output_model=Output,
    parts=PARTS,
    template=template,
    key_points=key_points,
    notes=notes,
    missing={"audit_event": "Audit activity in the window"},
    check=check,
    allow_partial=True,
)
