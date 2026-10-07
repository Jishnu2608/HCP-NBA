"""Append-only audit trail. This module only ever inserts."""

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

SYSTEM_ACTOR = "engine"
SYSTEM_ROLE = "system"
# Actors that are not accounts (no account id is looked up for them).
NON_ACCOUNT_ACTORS = frozenset({SYSTEM_ACTOR, "system", "anonymous", "simulator"})


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
    actor_user_id: int | None = None,
) -> AuditLog:
    if actor_user_id is None and actor not in NON_ACCOUNT_ACTORS:
        # The account behind the handle at this moment: durable even if the email is later
        # registered again by someone else.
        from sqlalchemy import select

        from app.models import User

        actor_user_id = db.scalar(select(User.id).where(User.username == actor))
    row = AuditLog(
        actor_user_id=actor_user_id,
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
