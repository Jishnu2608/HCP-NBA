"""M1, engagement opportunity brief: what a medical representative should know before engaging an
assigned HCP (permission `ai:engagement_brief`, the representative's own HCP scope).

Model-visible: specialty codes and preferred channel, the contact window, the latest engagement
records (channel, type, content reference, outcome), the open recommendation and open follow-up
work. HCP commercial fields (segment, value score, prescribing volume), identity and free text
stay out (field policy; commercial fields are PENDING_REVIEW). The contact limits are the
server's: the brief may never suggest contact before the window allows it.
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
    statement,
)
from app.core.permissions import Permission

ID = "engagement_brief"
VERSION = "1"
PROMPT_VERSION = "2"  # 2: evidence ids only in the citation lists
KINDS = ("hcp", "contact_window", "hcp_touch", "hcp_nba", "hcp_work")
PARTS = (
    Part("brief", "brief_citations", "Before you engage"),
    Part("next_step", "next_step_citations", "Next step"),
)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    brief: str = Field(min_length=1, max_length=600)
    brief_citations: list[str] = Field(min_length=1, max_length=10)
    next_step: str = Field(min_length=1, max_length=300)
    next_step_citations: list[str] = Field(min_length=1, max_length=4)


SYSTEM = (
    "You prepare a medical representative before they engage a healthcare professional.\n"
    "- brief: up to three sentences: the specialty and preferred channel, the contact window, "
    "and how recent engagement went (the latest outcomes as recorded).\n"
    "- next_step: one sentence: review the open recommendation or the open work if one is "
    "recorded. Never propose contact earlier than the contact window allows.\n"
    "- Under 80 words. Do not mention segments, value, prescribing volume or sales figures.\n"
    + COMMON_RULES
)

SOON = re.compile(
    r"\b(?:today|now|immediately|right away|straight away|asap|this week|tomorrow)\b", re.I
)
COMMERCIAL = re.compile(
    r"\b(?:segment\w*|value score|high[- ]value|prescrib\w* volume|rx volume|sales|revenue"
    r"|market share|decile|tier)\b",
    re.I,
)


def template(ctx: AuthorizedContext):
    hcp, window = items(ctx, "hcp"), items(ctx, "contact_window")
    touches, nba, work = items(ctx, "hcp_touch"), items(ctx, "hcp_nba"), items(ctx, "hcp_work")
    latest = touches[-1:]
    brief = statement(
        " ".join(i.text for i in hcp + window)
        + (f" Latest engagement: {latest[0].text}" if latest else " No engagement is recorded."),
        ids(hcp + window + latest),
    )
    if not ctx.facts.get("contact_allowed_now", True):
        step = statement(
            f"Contact limits allow no contact before {ctx.facts.get('next_allowed')}; plan the "
            "next step for then.",
            ids(window),
        )
    elif nba:
        step = statement(f"Review the {nba[0].text[0].lower()}{nba[0].text[1:]}", ids(nba))
    elif work:
        step = statement(f"Work the {work[0].text[0].lower()}{work[0].text[1:]}", ids(work[:1]))
    else:
        step = statement(
            "No open recommendation or open work is recorded; check the engagement history "
            "before planning contact.",
            ids(window) or ids(hcp),
        )
    return [brief, step]


def key_points(ctx: AuthorizedContext):
    return [
        statement(i.text, [i.id], "record") for category in KINDS for i in items(ctx, category)
    ][:16]


def notes(ctx: AuthorizedContext):
    return [
        "Commercial fields (segment, value score, prescribing volume) are never part of this "
        "brief.",
        "Contact limits and approved content are decided by the server; the brief never "
        "overrides them.",
    ]


def check(ctx: AuthorizedContext, out: BaseModel) -> tuple[list[GateFinding], int]:
    findings: list[GateFinding] = []
    for part in PARTS:
        text = getattr(out, part.field)
        if COMMERCIAL.search(text):
            findings.append(GateFinding(kind="commercial_claim", where=part.field))
    if not ctx.facts.get("contact_allowed_now", True) and SOON.search(out.next_step):
        findings.append(GateFinding(kind="contact_window_violation", where="next_step"))
    return findings, 0


SPEC = build_spec(
    id_=ID,
    version=VERSION,
    prompt_version=PROMPT_VERSION,
    permission=Permission.AI_ENGAGEMENT_BRIEF,
    subject_type="hcp",
    kinds=KINDS,
    system=SYSTEM,
    output_model=Output,
    parts=PARTS,
    template=template,
    key_points=key_points,
    notes=notes,
    missing={
        "hcp_touch": "Engagement history",
        "hcp_nba": "An open recommendation",
        "hcp_work": "Open follow-up work",
    },
    check=check,
)
