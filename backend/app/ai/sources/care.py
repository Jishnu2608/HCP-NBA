"""Care-request sources: one request, the care manager's queue, the HCP's consultations, the
patient's own record, and cycle-time figures.

Structured request fields (type, status, due date, timestamps) are model-eligible; the reason,
resolution, notes and typed labels never are (POL-01). Collections are paged and say when
they are truncated; aggregates are computed here, in code, from scoped timestamps only.
"""

from datetime import date, datetime
from statistics import median

from sqlalchemy.orm import Session

from app.ai.contracts import ContextRequest, SubjectType
from app.ai.policy import project
from app.ai.sources.base import Builder, Category, ContextSource
from app.core import clock
from app.models import User
from app.services import care_requests, consultations, self_service
from app.services.errors import NotFound


def _date(value) -> date | None:
    return None if value is None else date.fromisoformat(str(value)[:10])


def _label(value) -> str:
    return str(value).replace("_", " ")


def _request_text(r: dict, prefix: str = "Care request") -> str:
    due = f", due {r['due_date']}" if r.get("due_date") else ""
    overdue = " (overdue)" if r.get("overdue") else ""
    med = f", medication {r['medication']}" if r.get("medication") else ""
    cond = r.get("condition") or {}
    condition = f", condition {_label(cond['condition'])}" if cond.get("condition") else ""
    return (
        f"{prefix}: {_label(r['type'])}, {_label(r['status'])}{due}{overdue}{condition}{med}; "
        f"opened {str(r['created_at'])[:10]}, updated {str(r['updated_at'])[:10]}."
    )


def _notes_withheld(b: Builder, raw: dict) -> None:
    """Free text of a request is withheld from every model request; the reader sees it in the
    application's own screens."""
    for n in raw.get("notes") or []:
        if n.get("text"):
            b.withheld.append(n["text"])
    for key in ("reason", "resolution"):
        if raw.get(key):
            b.withheld.append(raw[key])


# --- One care request -------------------------------------------------------------------------


def read_request(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    try:
        rid = int(request.subject_id)
    except ValueError:
        raise NotFound("Care request not found") from None
    raw = care_requests.care_request(db, user, rid)
    b.as_of = clock.get_today(db)
    model = b.absorb(project("care_request", raw)).model
    _notes_withheld(b, raw)
    b.add(
        "care_request", _request_text(model), "care_requests.request", b.ref(rid),
        _date(model.get("created_at")), field_path="care_request.type,status,due_date",
        subject=raw.get("patient_id"),
    )  # fmt: skip
    b.mark("care_request", 1)
    b.facts["status"] = str(model["status"])


# --- The care manager's queue -----------------------------------------------------------------


def read_queue(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    queue = care_requests.care_queue(db, user)
    b.as_of = clock.get_today(db)
    start = request.cursor or 0
    page = queue.items[start : start + b.max_records]
    first_open = None
    for n, raw in enumerate(page, start + 1):
        model = b.absorb(project("care_request", raw), prefix=f"queue[{n}].").model
        _notes_withheld(b, raw)
        cid = b.add(
            "queue_item", _request_text(model, f"Queue #{n}"), "care_requests.queue",
            b.ref(raw["id"]), _date(model.get("created_at")),
            field_path="care_request.type,status,due_date", subject=raw.get("patient_id"),
        )  # fmt: skip
        if first_open is None and model.get("status") != "closed":
            first_open = cid
    # The server's most urgent open request on this page (the queue is ordered most urgent
    # first; closed requests remain listed after the open ones).
    b.facts["first_open_item"] = first_open
    b.mark("queue_item", queue.total)
    if start + len(page) < queue.total:
        b.truncated.add("queue_item")
    counts = ", ".join(f"{_label(k)} {v}" for k, v in sorted(queue.counts.items())) or "none"
    # Counted by the server over the whole queue, so no reader of the context has to count.
    overdue = sum(1 for raw in queue.items if raw.get("overdue"))
    b.add(
        "queue_counts",
        f"Requests by status: {counts}. In the queue: {queue.total}; overdue: {overdue}.",
        "care_requests.queue", "counts",
    )  # fmt: skip
    b.mark("queue_counts", 1)
    b.facts["queue_total"] = queue.total
    b.facts["overdue"] = overdue
    b.facts["counts"] = dict(sorted(queue.counts.items()))
    b.facts["next_cursor"] = start + len(page) if start + len(page) < queue.total else None


# --- The HCP's consultations ------------------------------------------------------------------


def read_consultations(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    view = consultations.hcp_consultations(db, user, refresh=False)
    b.as_of = clock.get_today(db)
    start = request.cursor or 0
    page = view["items"][start : start + b.max_records]
    for n, raw in enumerate(page, start + 1):
        model = b.absorb(project("consultation", raw), prefix=f"consultation[{n}].").model
        _notes_withheld(b, raw)
        b.add(
            "consultation",
            _request_text(model, f"Consultation #{n} ({_label(model['group'])})")
            + f" Patient age {model['patient_age']}.",
            "consultations", b.ref(raw["id"]), _date(model.get("created_at")),
            field_path="consultation.type,status,group", subject=raw.get("patient_id"),
        )  # fmt: skip
    b.mark("consultation", len(view["items"]))
    if start + len(page) < len(view["items"]):
        b.truncated.add("consultation")
    b.add(
        "consultation_counts",
        f"Consultations: waiting {view['waiting']}, with the care manager "
        f"{view['with_care_manager']}, history {view['history']}.",
        "consultations", "counts",
    )  # fmt: skip
    b.mark("consultation_counts", 1)


# --- The patient's own record -----------------------------------------------------------------


def read_self(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    health = self_service.own_health(db, user, refresh=False)
    b.as_of = clock.get_today(db)
    model = b.absorb(project("health", health)).model
    for r, raw in zip(model.get("requests") or [], health.get("requests") or [], strict=True):
        _notes_withheld(b, raw)
        b.add(
            "own_request", _request_text(r, "Your request"), "self.health.requests",
            b.ref(raw["id"]), _date(r.get("created_at")),
        )  # fmt: skip
    b.mark("own_request", len(health.get("requests") or []))
    for c, raw in zip(model.get("conditions") or [], health.get("conditions") or [], strict=True):
        code = "other condition" if c["condition"] == "other" else _label(c["condition"])
        b.add(
            "own_condition", f"Condition: {code}, {c['status']}.", "self.health.conditions",
            b.ref(raw["id"]), _date(c.get("confirmed_at") or c.get("reported_at")),
        )  # fmt: skip
    b.mark("own_condition", len(health.get("conditions") or []))
    meds = model.get("medications") or []
    for m, raw in zip(meds, health.get("medications") or [], strict=True):
        b.add(
            "own_medication", f"Medication: {m['drug_name']}, {_label(m['review_status'])}.",
            "self.health.medications", b.ref(raw["therapy_id"]), _date(m.get("start_date")),
        )  # fmt: skip
    b.mark("own_medication", len(meds))
    for n in health.get("notes") or []:
        if n.get("text"):
            b.withheld.append(n["text"])


# --- Cycle times ------------------------------------------------------------------------------


def _days(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return round((b - a).total_seconds() / 86400, 1)


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]


def workflow_metrics(rows) -> list[dict]:
    """Per request type: count, still open, and median / 90th percentile days for each step."""
    out = []
    for type_ in sorted({r.type for r in rows}):
        group = [r for r in rows if r.type == type_]
        for metric, pairs in (
            ("opened_to_closed", [(r.created_at, r.closed_at) for r in group]),
            ("opened_to_routed", [(r.created_at, r.routed_at) for r in group]),
            ("routed_to_answered", [(r.routed_at, r.responded_at) for r in group]),
        ):
            values = [d for a, c in pairs if (d := _days(a, c)) is not None]
            out.append(
                {
                    "metric": metric,
                    "type": type_,
                    "count": len(values),
                    "median_days": round(median(values), 1) if values else None,
                    "p90_days": _p90(values),
                    "open": sum(1 for r in group if r.status != "closed"),
                }
            )
    return out


WORKFLOW_SAMPLE = 500


def read_workflow(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    rows = care_requests.request_times(db, user, limit=WORKFLOW_SAMPLE + 1)
    truncated = len(rows) > WORKFLOW_SAMPLE
    rows = rows[:WORKFLOW_SAMPLE]
    b.as_of = clock.get_today(db)
    metrics = workflow_metrics(rows)
    for m in metrics:
        model = b.absorb(project("care_workflow_metric", m)).model
        if not model["count"]:
            continue
        b.add(
            "cycle_time",
            f"{_label(model['metric'])} for {_label(model['type'])}: {model['count']} requests, "
            f"median {model['median_days']} days, 90th percentile {model['p90_days']} days; "
            f"{model['open']} still open.",
            "care_requests.request_times", f"{model['type']}:{model['metric']}",
            field_path="care_workflow_metric",
        )  # fmt: skip
    if rows:
        start = min(r.created_at for r in rows).date()
        end = max(r.created_at for r in rows).date()
        b.add(
            "cycle_time",
            f"Sample: {len(rows)} care requests opened {start} to {end} (newest first, at most "
            f"{WORKFLOW_SAMPLE}).",
            "care_requests.request_times", "sample", field_path="care_workflow_metric",
        )  # fmt: skip
        b.facts["window"] = [start.isoformat(), end.isoformat()]
    b.mark("cycle_time", len(rows))
    if truncated:
        b.truncated.add("cycle_time")
    b.facts["sampled"] = len(rows)
    b.facts["truncated"] = truncated
    b.facts["metrics"] = metrics


REQUEST = ContextSource(
    id="care_request",
    domain="Care requests (staff)",
    subject_types=(SubjectType.CARE_REQUEST,),
    categories=(Category("care_request", "care_request", "Type, status, due date, timestamps"),),
    read=read_request,
    read_services=("services.care_requests.care_request",),
)
QUEUE = ContextSource(
    id="care_queue",
    domain="Care-request queue (care manager)",
    subject_types=(SubjectType.CARE_QUEUE,),
    categories=(
        Category("queue_item", "care_request", "Requests of the reader's panel, most urgent first"),
        Category("queue_counts", "care_request", "Requests per status", freshness="current"),
    ),
    read=read_queue,
    read_services=("services.care_requests.care_queue",),
    principal_dependent=True,
)
CONSULTATIONS = ContextSource(
    id="consultation_queue",
    domain="Consultations routed to the signed-in HCP",
    subject_types=(SubjectType.CONSULTATION_QUEUE,),
    categories=(
        Category("consultation", "consultation", "Consultations: type, status, group, age"),
        Category(
            "consultation_counts", "consultation", "Consultations per group", freshness="current"
        ),
    ),
    read=read_consultations,
    read_services=("services.consultations.hcp_consultations",),
    principal_dependent=True,
)
PATIENT_SELF = ContextSource(
    id="patient_self",
    domain="A patient's own health record",
    subject_types=(SubjectType.PATIENT_SELF,),
    categories=(
        Category("own_request", "care_request", "The patient's visible requests: type, status"),
        Category("own_condition", "condition", "Conditions: code and status"),
        Category("own_medication", "medication", "Medications: approved name and review state"),
    ),
    read=read_self,
    read_services=("services.self_service.own_health",),
    principal_dependent=True,
)
WORKFLOW = ContextSource(
    id="care_workflow",
    domain="Care-request cycle times (aggregate)",
    subject_types=(SubjectType.CARE_WORKFLOW,),
    categories=(
        Category(
            "cycle_time",
            "care_workflow_metric",
            "Median and p90 days per step",
            freshness="current",
        ),
    ),
    read=read_workflow,
    read_services=("services.care_requests.request_times",),
    principal_dependent=True,
)
