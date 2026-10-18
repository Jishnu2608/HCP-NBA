"""C1, coordination work queue brief: a read-out of the care manager's own care-request queue
(permission `ai:queue_brief`, the reader's patient scope).

The server computes every figure (counts by status, queue size, overdue requests, over the whole
queue) and the order (most urgent first). The model words them; it may not compute its own
figures (`derived_number`) or reorder the queue: the item to start with is the server's first
(`reordered_queue`). Request reasons, notes and patient identity stay out (field policy).
"""

from pydantic import BaseModel, ConfigDict, Field

from app.ai.contracts import AuthorizedContext, CategoryState, GateFinding
from app.ai.operations.common import (
    COMMON_RULES,
    Part,
    build_spec,
    ids,
    items,
    literal_numbers,
    statement,
)
from app.core.permissions import Permission

ID = "queue_brief"
VERSION = "1"
PROMPT_VERSION = "2"  # 2: evidence ids only in the citation lists
KINDS = ("queue_item", "queue_counts")
PARTS = (
    Part("overview", "overview_citations", "Your queue"),
    Part("start_with", "start_with_citations", "Start with"),
)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overview: str = Field(min_length=1, max_length=400)
    overview_citations: list[str] = Field(min_length=1, max_length=4)
    start_with: str = Field(min_length=1, max_length=300)
    start_with_citations: list[str] = Field(min_length=1, max_length=2)


SYSTEM = (
    "You write a short read-out of a care manager's care-request queue.\n"
    "- overview: one or two sentences on the requests by status, the queue size and overdue "
    "requests, repeating the figures exactly as the queue counts item states them.\n"
    "- start_with: one sentence on the most urgent open request, which the system already "
    "chose: the first queue item that is not closed. Never reorder the queue or pick another "
    "item.\n"
    "- Under 60 words.\n" + COMMON_RULES
)


def template(ctx: AuthorizedContext):
    counts, queue = items(ctx, "queue_counts"), items(ctx, "queue_item")
    overview = statement(counts[0].text if counts else "No queue counts are recorded.",
                         ids(counts) or ids(queue[:1]))  # fmt: skip
    first = _first_open(ctx)
    start = statement(
        f"Start with {first.text[0].lower()}{first.text[1:]}" if first
        else "No open care request is in the queue.",
        [first.id] if first else ids(counts) or ids(queue[:1]),
    )  # fmt: skip
    return [overview, start]


def _first_open(ctx: AuthorizedContext):
    """The server's most urgent open request (the queue lists closed requests after them)."""
    wanted = ctx.facts.get("first_open_item")
    return next((i for i in items(ctx, "queue_item") if i.id == wanted), None)


def key_points(ctx: AuthorizedContext):
    return [statement(i.text, [i.id], "record")
            for i in items(ctx, "queue_counts") + items(ctx, "queue_item")[:10]]  # fmt: skip


def notes(ctx: AuthorizedContext):
    out = [
        "The order is the server's (most urgent first), and the counts are computed by the "
        "server over the whole queue."
    ]
    total = ctx.facts.get("queue_total")
    shown = len(items(ctx, "queue_item"))
    if ctx.state_of("queue_item") is CategoryState.TRUNCATED and total:
        out.append(f"Showing the first {shown} of {total} requests.")
    return out


def check(ctx: AuthorizedContext, out: BaseModel) -> tuple[list[GateFinding], int]:
    findings: list[GateFinding] = []
    queue, counts = ids(items(ctx, "queue_item")), set(ids(items(ctx, "queue_counts")))
    if counts and not counts & set(out.overview_citations):
        findings.append(GateFinding(kind="uncited_counts", where="overview"))
    first = _first_open(ctx)
    cited_items = [c for c in out.start_with_citations if c in queue]
    if (first and cited_items != [first.id]) or (not first and cited_items):
        findings.append(GateFinding(kind="reordered_queue", where="start_with"))
    for part in PARTS:
        findings += literal_numbers(ctx, getattr(out, part.field), part.field)
    return findings, 0


SPEC = build_spec(
    id_=ID,
    version=VERSION,
    prompt_version=PROMPT_VERSION,
    permission=Permission.AI_QUEUE_BRIEF,
    subject_type="care_queue",
    kinds=KINDS,
    system=SYSTEM,
    output_model=Output,
    parts=PARTS,
    template=template,
    key_points=key_points,
    notes=notes,
    missing={"queue_item": "An open care request"},
    check=check,
    allow_partial=True,
)
