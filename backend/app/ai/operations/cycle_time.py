"""X2, workflow cycle-time analyser: care-request cycle times computed in code, for the care
manager (permission `ai:cycle_time`, the reader's patient scope).

Every figure (count, median and 90th percentile in days, still open) is computed by the backend
over a stated sample and window; definitions, sample size, window and missing metrics are
server notes on every result. The model only rewords the figures: it may not compute new ones
(`derived_number`) or invent causes (`invented_cause`). The deterministic template states the
figures fully, so model wording adds presentation only; this operation is meant to stay in
template mode unless the owner decides otherwise.
"""

import re

from pydantic import BaseModel, ConfigDict, Field

from app.ai.contracts import AuthorizedContext, CategoryState, GateFinding
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

ID = "cycle_time"
VERSION = "1"
PROMPT_VERSION = "2"  # 2: evidence ids only in the citation lists
KINDS = ("cycle_time",)
PARTS = (Part("summary", "summary_citations", "Cycle times"),)
DEFINITIONS = {
    "opened_to_routed": "Opened to routed: days from a request's creation to its routing to an "
    "HCP.",
    "routed_to_answered": "Routed to answered: days from routing to the HCP's response.",
    "opened_to_closed": "Opened to closed: days from a request's creation to its closing.",
}


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    summary_citations: list[str] = Field(min_length=1, max_length=12)


SYSTEM = (
    "You describe care-request cycle times the system computed: for each step and request "
    "type, the number of requests, the median and the 90th percentile in days, and how many "
    "are still open.\n"
    "- summary: two or three sentences repeating figures exactly as the items state them, and "
    "naming the sample.\n"
    "- Do not explain causes or speculate why, and do not recommend changes.\n"
    "- Under 80 words.\n" + COMMON_RULES
)

CAUSE = re.compile(
    r"\b(?:because|due to|caused|causes|owing to|as a result|results? from|driven by"
    r"|leads? to|led to|attributable|bottleneck\w*|so that|reason (?:is|was|being))\b",
    re.I,
)


def _metric_items(ctx):
    return [i for i in items(ctx, "cycle_time") if not i.text.startswith("Sample:")]


def template(ctx: AuthorizedContext):
    metrics = _metric_items(ctx)
    sample = [i for i in items(ctx, "cycle_time") if i.text.startswith("Sample:")]
    if not metrics:
        return [statement("No completed care-request steps are recorded in the sample.",
                          ids(sample))]  # fmt: skip
    text = " ".join(i.text[0].upper() + i.text[1:] for i in metrics[:6])
    return [statement(text + (f" {sample[0].text}" if sample else ""), ids(metrics[:6] + sample))]


def key_points(ctx: AuthorizedContext):
    return [statement(i.text, [i.id], "record") for i in items(ctx, "cycle_time")][:16]


def notes(ctx: AuthorizedContext):
    metrics = ctx.facts.get("metrics") or []
    used = {m["metric"] for m in metrics}
    out = [DEFINITIONS[k] for k in DEFINITIONS if k in used]
    window = ctx.facts.get("window")
    if window:
        out.append(
            f"Sample: {ctx.facts.get('sampled')} care requests opened {window[0]} to {window[1]}."
        )
    empty = [m for m in metrics if not m["count"]]
    if empty:
        out.append(
            "No figures yet for: "
            + "; ".join(f"{m['metric'].replace('_', ' ')} ({m['type'].replace('_', ' ')})"
                        for m in empty[:6]) + "."
        )  # fmt: skip
    if ctx.state_of("cycle_time") is CategoryState.TRUNCATED:
        out.append("The sample is capped; older requests are not included.")
    out.append("Computed in code; the written figures are complete without model wording.")
    return out[:8]


def check(ctx: AuthorizedContext, out: BaseModel) -> tuple[list[GateFinding], int]:
    findings = pattern_finding(CAUSE, "invented_cause", out.summary, "summary")
    findings += literal_numbers(ctx, out.summary, "summary")
    return findings, 0


SPEC = build_spec(
    id_=ID,
    version=VERSION,
    prompt_version=PROMPT_VERSION,
    permission=Permission.AI_CYCLE_TIME,
    subject_type="care_workflow",
    kinds=KINDS,
    system=SYSTEM,
    output_model=Output,
    parts=PARTS,
    template=template,
    key_points=key_points,
    notes=notes,
    missing={"cycle_time": "Care-request activity"},
    check=check,
    allow_partial=True,
)
