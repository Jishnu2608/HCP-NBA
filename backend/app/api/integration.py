"""The governed CRM hand-off feed (integration/crm.py): field map, JSON and CSV export."""

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app import audit
from app.api.deps import require_permission
from app.core import clock
from app.core.db import get_db
from app.core.permissions import Permission
from app.integration import crm
from app.models import User

router = APIRouter(prefix="/api/integration/crm", tags=["integration"])

exporters = require_permission(Permission.HANDOFF_EXPORT)


@router.get("/field-map")
def field_map(_: User = Depends(exporters)) -> dict:
    return {
        "fields": crm.field_map(),
        "statuses": list(crm.HANDED_OFF),
        "max_rows": crm.MAX_ROWS,
        "max_range_days": crm.MAX_RANGE_DAYS,
        "default_range_days": crm.DEFAULT_RANGE_DAYS,
    }


@router.get("/records", response_model=None)
def export_records(
    target_type: str | None = Query(None, max_length=8),
    record_status: str | None = Query(None, alias="status", max_length=16),
    since: date | None = None,
    until: date | None = None,
    limit: int = Query(1000),
    format: Literal["json", "csv"] = "json",
    user: User = Depends(exporters),
    db: Session = Depends(get_db),
) -> dict | Response:
    """Approved recommendations in the reader's scope, with delivery and outcome. Read-only;
    every call is audited (parameters and row count, never the rows)."""
    today = clock.get_today(db)
    try:
        params = crm.validate(target_type, record_status, since, until, limit, today)
    except crm.ExportError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "invalid_export", "message": str(exc)}
        ) from exc
    rows = crm.records(db, user, params)
    audit.record(
        db,
        "crm_export",
        "integration",
        "crm",
        actor=user.username,
        actor_role=user.role,
        detail={
            "format": format,
            "target_type": params.target_type,
            "status": params.status,
            "since": params.since.isoformat(),
            "until": params.until.isoformat(),
            "limit": params.limit,
            "rows": len(rows),
        },
    )
    db.commit()
    if format == "csv":
        return Response(
            crm.to_csv(rows),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="nba-handoff-{params.since}-{params.until}.csv"'
                )
            },
        )
    return {
        "since": params.since,
        "until": params.until,
        "count": len(rows),
        "truncated": len(rows) == params.limit,
        "fields": crm.FIELD_NAMES,
        "records": rows,
    }
