"""Append-only audit trail. This module only ever inserts."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

SYSTEM_ACTOR = "engine"
SYSTEM_ROLE = "system"


def record(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str | int,
    *,
    actor: str = SYSTEM_ACTOR,
    actor_role: str = SYSTEM_ROLE,
    nba_id: int | None = None,
    compliance_ok: bool | None = None,
    consent_ok: bool | None = None,
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        actor=actor,
        actor_role=actor_role,
        nba_id=nba_id,
        compliance_ok=compliance_ok,
        consent_ok=consent_ok,
        reason=reason,
        detail=detail,
    )
    db.add(row)
    return row
