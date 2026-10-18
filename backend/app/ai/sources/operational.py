"""Operational aggregates: AI telemetry, audit activity, the job queue, quality measures and one
account's access scope. Figures only, computed by the read services; no record-level detail,
no actor, no subject id, no text.
"""

from sqlalchemy.orm import Session

from app.ai.contracts import ContextRequest, SubjectType
from app.ai.policy import project
from app.ai.sources.base import Builder, Category, ContextSource, SourceUnavailable
from app.core import clock
from app.models import User
from app.services import access, ai_telemetry, analytics, audit, jobs
from app.services.errors import NotFound

AI_ACTIONS = (
    "ai_generation_requested",
    "ai_generation_completed",
    "ai_generation_fallback",
    "ai_generation_failed",
    "ai_generation_rejected",
    "ai_diagnostics_run",
)


def _label(value) -> str:
    return str(value).replace("_", " ")


def read_telemetry(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    t = ai_telemetry.ai_telemetry(db, user)
    b.as_of = clock.get_today(db)
    if not t.available:
        b.unavailable.add("generation_outcome")
        raise SourceUnavailable("ai_tables_missing")
    for row in t.rows:
        m = b.absorb(project("ai_telemetry_row", row.model_dump())).model
        failure = f", failure {_label(m['failure'])}" if m.get("failure") else ""
        timing = f", median {m['median_ms']} ms, p95 {m['p95_ms']} ms" if m["median_ms"] else ""
        b.add(
            "generation_outcome",
            f"{m['operation']}: {m['count']} {_label(m['status'])}{failure}{timing}; "
            f"{m['subjects']} distinct subjects.",
            "ai_telemetry", f"{m['operation']}:{m['status']}:{m['failure'] or '-'}",
        )  # fmt: skip
    b.mark("generation_outcome", t.considered)
    if t.truncated:
        b.truncated.add("generation_outcome")
    b.facts["active"] = t.active


AUDIT_DAYS = 14


def read_audit(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    rows = audit.audit_activity(db, user, days=AUDIT_DAYS)
    b.as_of = clock.get_today(db)
    b.facts["window_days"] = AUDIT_DAYS
    b.facts["totals"] = {}
    for row in rows:
        b.facts["totals"][row.action] = b.facts["totals"].get(row.action, 0) + row.count
    for row in rows:
        m = b.absorb(project("audit_activity_row", row.model_dump(mode="json"))).model
        unit = "event" if m["count"] == 1 else "events"
        b.add(
            "audit_event", f"{m['day']}: {_label(m['action'])}, {m['count']} {unit}.",
            "audit.activity", f"{m['action']}:{m['day']}",
        )  # fmt: skip
    if rows:
        b.add(
            "audit_event", f"Window: the last {AUDIT_DAYS} days, up to {b.as_of}.",
            "audit.activity", "window",
        )  # fmt: skip
    b.mark("audit_event", len(rows))


def read_jobs(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    rows = jobs.job_queue(db, user)
    b.as_of = clock.get_today(db)
    for row in rows:
        m = b.absorb(project("job_queue_row", row.model_dump())).model
        age = f", oldest {m['oldest_minutes']} min" if m.get("oldest_minutes") is not None else ""
        b.add(
            "job_state", f"Jobs {_label(m['kind'])} {m['status']}: {m['count']}{age}.",
            "jobs.queue", f"{m['kind']}:{m['status']}",
        )  # fmt: skip
    b.mark("job_state", len(rows))


def read_measures(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    result = analytics.quality_measures(db, user)
    b.as_of = clock.get_today(db)
    rows = result.get("measures") or []
    for r in rows:
        current = r.get("current") or {}
        persistent = r.get("currently_persistent") or {}
        row = {
            "measure": r["measure"],
            "patients": current.get("n"),
            "suppressed": bool(current.get("suppressed")),
            "adherent_share": current.get("rate"),
            "persistence_share": persistent.get("rate"),
            "courses": r.get("courses"),
        }
        # Figures are pending review: the item is server text (category not model-visible).
        m = b.absorb(project("quality_measure", row)).deterministic
        figure = (
            "suppressed (group below the minimum size)"
            if m["suppressed"]
            else f"adherent share {m['adherent_share']} of {m['patients']} patients"
        )
        b.add(
            "measure", f"Measure {m['measure']} ({result.get('scope', 'all')} scope): {figure}.",
            "analytics.quality_measures", m["measure"],
        )  # fmt: skip
    b.mark("measure", len(rows))


def read_access(db: Session, user: User, request: ContextRequest, b: Builder) -> None:
    try:
        account_id = int(request.subject_id)
    except ValueError:
        raise NotFound("Account not found") from None
    scope = access.access_scope(db, user, account_id)
    b.as_of = clock.get_today(db)
    # Deterministic only: permission names and counts feed server-written text and checks.
    b.absorb(
        project(
            "access_scope",
            {
                "account_ref": b.ref(account_id),
                "role": scope.role,
                "permissions": scope.permissions,
                "assigned_patients": scope.assigned_patients,
                "assigned_hcps": scope.assigned_hcps,
                "status": scope.status,
            },
        )
    )
    b.facts["access"] = scope.model_dump()
    b.mark("access", 1)


TELEMETRY = ContextSource(
    id="ai_telemetry",
    domain="AI generation telemetry (aggregate)",
    subject_types=(SubjectType.AI_TELEMETRY,),
    categories=(
        Category(
            "generation_outcome", "ai_telemetry_row", "Outcomes per operation", freshness="current"
        ),
    ),
    read=read_telemetry,
    read_services=("services.ai_telemetry.ai_telemetry",),
)
AUDIT = ContextSource(
    id="audit_activity",
    domain="Audit activity (aggregate)",
    subject_types=(SubjectType.AUDIT_ACTIVITY,),
    categories=(
        Category(
            "audit_event", "audit_activity_row", "Events per action and day", freshness="current"
        ),
    ),
    read=read_audit,
    read_services=("services.audit.audit_activity",),
)
JOBS = ContextSource(
    id="job_queue",
    domain="Background jobs (aggregate)",
    subject_types=(SubjectType.JOB_QUEUE,),
    categories=(
        Category("job_state", "job_queue_row", "Jobs per kind and status", freshness="current"),
    ),
    read=read_jobs,
    read_services=("services.jobs.job_queue",),
)
MEASURES = ContextSource(
    id="quality_measures",
    domain="Quality measures (aggregate, suppressed below the minimum group)",
    subject_types=(SubjectType.QUALITY_MEASURES,),
    categories=(
        Category(
            "measure",
            "quality_measure",
            "Adherence and persistence shares (pending review: server text only)",
            model=False,
            freshness="current",
        ),
    ),
    read=read_measures,
    read_services=("services.analytics.quality_measures",),
    principal_dependent=True,
)
ACCESS = ContextSource(
    id="access_scope",
    domain="Account access boundary",
    subject_types=(SubjectType.ACCESS_SCOPE,),
    categories=(
        Category(
            "access",
            "access_scope",
            "Role, permissions, assignment counts",
            model=False,
            freshness="current",
        ),
    ),
    read=read_access,
    read_services=("services.access.access_scope",),
)
