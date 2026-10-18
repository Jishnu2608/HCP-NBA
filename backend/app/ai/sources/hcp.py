"""HCP 360 for a staff reader (`services.hcps.hcp_profile`).

Model-eligible: specialty codes, channel preference, the contact window computed by the engine,
the structure of the engagement history, the open recommendation and open commercial work
(kind, status, due date). Restricted: identity, NPI, organisation, location and upstream
identity records. Pending a governance decision: commercial value fields and the HCP's
de-identified group adherence. The history's "sent by" depends on the reader, so the view is
principal-dependent.
"""

import json
from datetime import date

from sqlalchemy.orm import Session

from app.ai.contracts import ContextRequest, SubjectType
from app.ai.policy import project
from app.ai.sources.base import Builder, Category, ContextSource
from app.models import User
from app.services import hcps

SOURCE = "hcp_360"
TOUCH_LIMIT = 5
WORK_LIMIT = 10


def _date(value) -> date | None:
    return None if value is None else date.fromisoformat(str(value)[:10])


def read(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    profile = hcps.hcp_profile(db, user, request.subject_id)
    b.as_of = profile.as_of_date
    raw = json.loads(profile.model_dump_json())
    model = b.absorb(project("hcp_360", raw)).model
    b.identifiers += [profile.hcp_id, *(p for p in profile.name.split() if len(p) >= 3)]
    subject = profile.hcp_id

    codes = [s["code"] for s in model["specialties"]]
    text = "HCP specialties: " + (", ".join(codes) or "not configured") + "."
    if model.get("channel_affinity"):
        text += f" Preferred channel: {model['channel_affinity']}."
    b.add("hcp", text, f"{SOURCE}.profile", b.ref(subject), subject=subject)
    b.mark("hcp", 1)

    w = model["contact_window"]
    allowed = "allowed now" if w["allowed_now"] else f"next allowed {w['next_allowed']}"
    # For deterministic text and checks (a brief must never suggest contact the limits refuse).
    b.facts["contact_allowed_now"] = bool(w["allowed_now"])
    b.facts["next_allowed"] = str(w["next_allowed"]) if w.get("next_allowed") else None
    last = f"; last contact {w['last_contact']}" if w.get("last_contact") else ""
    b.add(
        "contact_window",
        f"Contact window: {allowed}{last}; minimum gap {w['min_gap_days']} days.",
        f"{SOURCE}.contact_window", b.ref(subject), _date(w.get("last_contact")),
        field_path="contact_window", as_of=profile.as_of_date, subject=subject,
    )  # fmt: skip
    b.mark("contact_window", 1)

    rows = list(zip(profile.interactions, model["interactions"], strict=True))
    for raw_i, r in reversed(rows[:TOUCH_LIMIT]):
        day = _date(r["ts"])
        content = f" ({r['content_id']} v{r['content_version']})" if r.get("content_id") else ""
        b.add(
            "hcp_touch",
            f"{day} {r['channel_label']} {r['type_label'].lower()}{content}; "
            f"{str(r['outcome']).replace('_', ' ')}.",
            f"{SOURCE}.interactions", raw_i.id, day, field_path="interaction",
        )  # fmt: skip
    b.mark("hcp_touch", len(profile.interactions))

    nba = model.get("open_nba")
    if nba:
        b.add(
            "hcp_nba",
            f"Open recommendation: {nba['action_label']} by {nba['channel_label']}"
            + (f", content {nba['content_id']}" if nba.get("content_id") else "")
            + f", {str(nba['status']).replace('_', ' ')}.",
            f"{SOURCE}.open_nba", profile.open_nba.id, _date(nba.get("scheduled_for")),
        )  # fmt: skip
    b.mark("hcp_nba", 1 if nba else 0)

    work = model["open_work"]
    for task, raw_t in list(zip(work, profile.open_work, strict=True))[:WORK_LIMIT]:
        due = f", due {task['due_date']}" if task.get("due_date") else ""
        b.add(
            "hcp_work",
            f"Open work: {str(task['kind']).replace('_', ' ')}, {task['status']}{due}"
            + (" (overdue)" if task.get("overdue") else "") + ".",
            f"{SOURCE}.open_work", raw_t["id"], _date(task.get("created_at")),
            field_path="hcp_task.kind,status,due_date",
        )  # fmt: skip
    b.mark("hcp_work", len(work))


SOURCE_DEF = ContextSource(
    id=SOURCE,
    domain="HCP 360 (staff)",
    subject_types=(SubjectType.HCP,),
    categories=(
        Category("hcp", "hcp_360", "Specialty codes and preferred channel", freshness="current"),
        Category("contact_window", "contact_window", "When the contact limits allow a touch"),
        Category(
            "hcp_touch",
            "interaction",
            "Latest engagement records",
            terms=(
                "engagement",
                "engagements",
                "interaction",
                "interactions",
                "contact",
                "contacts",
                "email",
                "emails",
                "visit",
                "visits",
                "message",
                "messages",
                "response",
                "responses",
                "reply",
                "replies",
            ),
        ),
        Category(
            "hcp_nba",
            "recommendation_summary",
            "Open recommendation",
            terms=("recommendation", "recommendations"),
        ),
        Category("hcp_work", "hcp_task", "Open commercial work: kind, status, due date"),
    ),
    read=read,
    read_services=("services.hcps.hcp_profile",),
    principal_dependent=True,
)
