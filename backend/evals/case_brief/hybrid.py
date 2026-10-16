"""Hybrid Case Brief (prompt v5): the backend writes the facts, the model writes two sentences.

Written by code, from the record: identity, key points (dates, days covered, consent, outreach,
the open recommendation, a refill conflict) and the not-recorded list. Written by the model:
`summary` and `suggested_focus`, each with the ids of the items it used. The composed brief
then goes through the same gate as before.
"""

from pydantic import BaseModel, ConfigDict, Field

from .baseline import template_brief
from .cases import Case, context_items
from .checks import DRUG_TERMS, _facts, _terms
from .schema import CaseBrief


class ModelNarrative(BaseModel):
    """Everything the model may write in a hybrid brief."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    summary_citations: list[str] = Field(min_length=1, max_length=8)
    suggested_focus: str = Field(min_length=1, max_length=400)
    focus_citations: list[str] = Field(min_length=1, max_length=6)


def json_schema(item_ids: list[str] | None = None) -> dict:
    """Schema handed to the runtime. With `item_ids`, citations may only be those ids (enum),
    so the constrained decoder cannot write any other id."""
    raw = ModelNarrative.model_json_schema()
    props = {
        k: {kk: vv for kk, vv in v.items() if kk != "title"} for k, v in raw["properties"].items()
    }
    if item_ids:
        for key in ("summary_citations", "focus_citations"):
            props[key]["items"] = {"type": "string", "enum": list(item_ids)}
    return {k: v for k, v in raw.items() if k != "title"} | {"properties": props}


def compose(case: Case, narrative: ModelNarrative) -> CaseBrief:
    """The brief a reader would see: deterministic parts plus the model's two sentences."""
    base = template_brief(case)
    return CaseBrief(
        summary=narrative.summary,
        key_points=base.key_points,
        suggested_focus=narrative.suggested_focus,
        not_recorded=base.not_recorded,
    )


def check_citations(case: Case, narrative: ModelNarrative, view: dict) -> tuple[list[dict], int]:
    """Invalid citation ids (critical) and miscited sentences for the model's two parts."""
    item_text = dict(context_items(case, **view))
    ctx_all = " ".join(item_text.values())
    ctx_dates, ctx_nums, ctx_ids = _facts(ctx_all)
    critical: list[dict] = []
    miscited = 0
    for where, text, cites in (
        ("summary", narrative.summary, narrative.summary_citations),
        ("suggested_focus", narrative.suggested_focus, narrative.focus_citations),
    ):
        for cid in cites:
            if cid not in item_text:
                critical.append({"kind": "invalid_citation", "where": where, "value": cid})
        cited = " ".join(item_text.get(c, "") for c in cites)
        d_c, n_c, i_c = _facts(cited)
        d_s, n_s, i_s = _facts(text)
        n_c = n_c | {round(n * 100, 4) for n in n_c if 0 < n <= 1}
        if (
            (d_s & ctx_dates) - d_c
            or (i_s & ctx_ids) - i_c
            or (n_s & ctx_nums) - n_c
            or (_terms(text, DRUG_TERMS) & _terms(ctx_all, DRUG_TERMS)) - _terms(cited, DRUG_TERMS)
        ):
            miscited += 1
    return critical, miscited
