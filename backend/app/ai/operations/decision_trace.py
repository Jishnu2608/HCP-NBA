"""X1, NBA decision trace explainer: why the engine chose a recommendation, for the care manager
who reviews it (permission `ai:decision_trace`, the reader's own recommendation scope).

Only recorded structure is used: the decision (action, channel, content id, schedule, status),
its coded reasons and every option the engine weighed with its gate results. Rationale text,
scores, drafts, the audit trail and the person's identity stay out (field policy). The model
words the explanation; it never decides, approves, rejects or changes the recommendation, and
every code it names must sit in the items it cites.
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
    pattern_finding,
    statement,
)
from app.core.permissions import Permission

ID = "decision_trace"
VERSION = "1"
# 2: compliance reasons left to the server, cite the decision; 3: no ids in text;
# 4: cite every option mentioned.
PROMPT_VERSION = "4"
KINDS = ("decision", "reason", "candidate", "response")
PARTS = (
    Part("explanation", "explanation_citations", "Why this recommendation"),
    Part("alternatives", "alternatives_citations", "Other options considered"),
)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanation: str = Field(min_length=1, max_length=500)
    explanation_citations: list[str] = Field(min_length=1, max_length=12)
    alternatives: str = Field(min_length=1, max_length=400)
    alternatives_citations: list[str] = Field(min_length=1, max_length=10)


SYSTEM = (
    "You explain to a care manager why the engine chose one recommendation, using only its "
    "recorded reasons and the options it weighed.\n"
    "- explanation: one or two sentences on why this option was chosen, from the recorded "
    "reason codes. Always cite the decision item (ctx:decision:1) and the reason items you "
    "use. Leave out the compliance reasons (consent and MLR checks): the system states those, "
    "so never mention consent.\n"
    "- alternatives: one sentence on the other options and why they were not chosen, from "
    "their gate failures, citing every option you mention; if no other option is recorded, "
    "say so.\n"
    "- Under 70 words in all. Do not say the recommendation should be approved, rejected, "
    "changed or overridden.\n" + COMMON_RULES
)

REASON = re.compile(r"Reason \((?P<kind>[a-z_]+)\): (?P<code>[a-z0-9_]+)\.")
CHANGE = re.compile(
    r"\b(?:override\w*|overrule\w*|should (?:be )?(?:approved|rejected|changed|sent|cancel\w*"
    r"|replaced)|change (?:the|this) recommendation|switch (?:to|it)|instead (?:send|use|choose"
    r"|recommend))\b",
    re.I,
)
CODE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _human(code: str) -> str:
    return code.replace("_", " ")


def template(ctx: AuthorizedContext):
    decision = items(ctx, "decision")
    reasons = items(ctx, "reason")
    grouped: dict[str, list[str]] = {}
    for r in reasons:
        m = REASON.search(r.text)
        if m:
            grouped.setdefault(m["kind"], []).append(_human(m["code"]))
    why = "; ".join(f"{kind}: {', '.join(codes)}" for kind, codes in grouped.items())
    explanation = statement(
        (decision[0].text if decision else "This recommendation was recorded.")
        + (f" Recorded reasons: {why}." if why else " No reasons are recorded."),
        ids(decision + reasons),
    )
    others = [c for c in items(ctx, "candidate") if ": chosen;" not in c.text]
    alternatives = statement(
        " ".join(c.text for c in others[:4]) if others else "No other option is recorded.",
        ids(others[:4]) or ids(decision),
    )
    return [explanation, alternatives]


def key_points(ctx: AuthorizedContext):
    points = [statement(i.text, [i.id], "record") for i in items(ctx, "decision")]
    reasons = items(ctx, "reason")
    if reasons:
        codes = ", ".join(_human(m["code"]) for r in reasons if (m := REASON.search(r.text)))
        points.append(statement(f"Recorded reason codes: {codes}.", ids(reasons), "record"))
    points += [statement(i.text, [i.id], "record") for i in items(ctx, "candidate")[:10]]
    points += [statement(i.text, [i.id], "record") for i in items(ctx, "response")]
    return points


def notes(ctx: AuthorizedContext):
    return [
        "Built from the engine's recorded reason and gate codes only. The explanation never "
        "changes the recommendation: approving, rejecting or sending it stays your decision.",
    ]


def check(ctx: AuthorizedContext, out: BaseModel) -> tuple[list[GateFinding], int]:
    findings: list[GateFinding] = []
    decision = set(ids(items(ctx, "decision")))
    if decision and not decision & set(out.explanation_citations):
        findings.append(GateFinding(kind="uncited_decision", where="explanation"))
    by_id = {i.id: i.text for i in ctx.model_items}
    for part in PARTS:
        text, cites = getattr(out, part.field), getattr(out, part.citations)
        findings += pattern_finding(CHANGE, "recommendation_change", text, part.field)
        cited = " ".join(by_id.get(c, "") for c in cites)
        # A reason or gate code is named only with the item that records it.
        if set(CODE.findall(text)) - set(CODE.findall(cited)):
            findings.append(GateFinding(kind="miscited_code", where=part.field))
    return findings, 0


SPEC = build_spec(
    id_=ID,
    version=VERSION,
    prompt_version=PROMPT_VERSION,
    permission=Permission.AI_DECISION_TRACE,
    subject_type="recommendation",
    kinds=KINDS,
    system=SYSTEM,
    output_model=Output,
    parts=PARTS,
    template=template,
    key_points=key_points,
    notes=notes,
    missing={"response": "A response to this recommendation"},
    check=check,
)
