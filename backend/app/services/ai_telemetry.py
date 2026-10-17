"""AI generation telemetry, minimised: outcomes, failure categories, durations and token counts
per operation. Subject ids never leave this module; distinct subjects are only counted.

Separately authorised (`ai:diagnostics`). On a database without the AI tables the answer says
so (`available: False`) instead of reporting zero generations as if all were well. Read only.
"""

from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import has_table
from app.core.permissions import Permission, can
from app.models import AiGeneration, AuditLog, User
from app.services.errors import Forbidden
from app.services.schemas import Contract

MAX_ROWS = 5000  # most recent generations considered


class TelemetryRow(Contract):
    operation: str
    status: str
    failure: str | None
    count: int
    median_ms: int | None
    p95_ms: int | None
    prompt_tokens: int | None
    output_tokens: int | None
    subjects: int


class RejectionRow(Contract):
    """Requests refused before any generation existed (forbidden, not found, template mode,
    rate limited, busy, context unavailable), by fixed reason code."""

    reason: str
    count: int


class AiTelemetry(Contract):
    available: bool
    considered: int  # generations read (at most MAX_ROWS)
    truncated: bool  # more generations exist than were read
    rows: list[TelemetryRow]
    active: int  # queued or running now
    rejections: list[RejectionRow] = []  # from the audit trail: codes and counts only


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


def ai_telemetry(db: Session, user: User) -> AiTelemetry:
    if not can(user, Permission.AI_DIAGNOSTICS):
        raise Forbidden("Your account cannot read AI telemetry.")
    if not has_table(db, AiGeneration.__tablename__):
        return AiTelemetry(available=False, considered=0, truncated=False, rows=[], active=0)
    gens = db.execute(
        select(
            AiGeneration.operation,
            AiGeneration.status,
            AiGeneration.failure,
            AiGeneration.duration_ms,
            AiGeneration.prompt_tokens,
            AiGeneration.output_tokens,
            AiGeneration.subject_type,
            AiGeneration.subject_id,
        )
        .order_by(AiGeneration.id.desc())
        .limit(MAX_ROWS + 1)
    ).all()
    truncated = len(gens) > MAX_ROWS
    gens = gens[:MAX_ROWS]
    groups: dict[tuple, list] = {}
    for g in gens:
        groups.setdefault((g.operation, g.status, g.failure), []).append(g)
    rows = []
    for (operation, status, failure), members in sorted(
        groups.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")
    ):
        durations = [m.duration_ms for m in members if m.duration_ms is not None]
        prompts = [m.prompt_tokens for m in members if m.prompt_tokens is not None]
        outputs = [m.output_tokens for m in members if m.output_tokens is not None]
        rows.append(
            TelemetryRow(
                operation=operation,
                status=status,
                failure=failure,
                count=len(members),
                median_ms=int(median(durations)) if durations else None,
                p95_ms=_p95(durations),
                prompt_tokens=sum(prompts) if prompts else None,
                output_tokens=sum(outputs) if outputs else None,
                subjects=len({(m.subject_type, m.subject_id) for m in members}),
            )
        )
    active = sum(1 for g in gens if g.status in ("queued", "running"))
    rejections = [
        RejectionRow(reason=reason or "unspecified", count=n)
        for reason, n in db.execute(
            select(AuditLog.reason, func.count())
            .where(AuditLog.action == "ai_generation_rejected")
            .group_by(AuditLog.reason)
            .order_by(AuditLog.reason)
        )
    ]
    return AiTelemetry(
        available=True,
        considered=len(gens),
        truncated=truncated,
        rows=rows,
        active=active,
        rejections=rejections,
    )
