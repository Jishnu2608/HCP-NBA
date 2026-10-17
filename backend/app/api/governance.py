"""Audit log, engine configuration and engine operations."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, cycle, jobs
from app.analytics import snapshots
from app.api.auth import set_session_cookie
from app.api.deps import require_permission
from app.api.schemas import StrictBody
from app.auth.repository import SqlUserRepository
from app.auth.service import account_out
from app.auth.sessions import sessions
from app.core import clock
from app.core.db import get_db
from app.core.engine_config import DEFAULTS, get_config, set_config, validate_value
from app.core.permissions import Permission
from app.datagen.generate import GenConfig, generate
from app.engagement import delivery
from app.models import EngineCycle, Job, ModelVersion, Nba, User
from app.models.enums import NbaStatus
from app.models.tables import utcnow
from app.services import audit as audit_service

router = APIRouter(prefix="/api", tags=["governance"])

auditors = require_permission(Permission.AUDIT_READ)
configurer = require_permission(Permission.CONFIG_MANAGE)
admin = require_permission(Permission.ENGINE_OPERATE)
model_readers = require_permission(Permission.MODELS_READ)


class BulkSendBody(StrictBody):
    limit: int = Field(default=300, ge=1, le=5000)


class ResetBody(StrictBody):
    patients: int = Field(default=3000, ge=50, le=20000)
    hcps: int = Field(default=300, ge=12, le=2000)


class ConfigValue(StrictBody):
    value: Any


@router.get("/audit")
def audit_log(
    action: str | None = Query(None, max_length=48),
    nba_id: int | None = None,
    actor: str | None = Query(None, max_length=254),
    entity_id: str | None = Query(None, max_length=64),
    mine: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(auditors),
    db: Session = Depends(get_db),
) -> dict:
    """Readers without audit:read:identified (Compliance) get account emails and patient /
    HCP ids masked: what happened, when and by which role, without who it was about."""
    page = audit_service.audit_entries(
        db, user, action=action, nba_id=nba_id, actor=actor, entity_id=entity_id, mine=mine,
        limit=limit, offset=offset,
    )  # fmt: skip
    return page.model_dump()


@router.get("/admin/config")
def read_config(_: User = Depends(configurer), db: Session = Depends(get_db)) -> dict:
    return {
        key: {"value": get_config(db, key), "default": default} for key, default in DEFAULTS.items()
    }


@router.put("/admin/config/{key}")
def write_config(
    key: str, body: ConfigValue, user: User = Depends(configurer), db: Session = Depends(get_db)
) -> dict:
    if key not in DEFAULTS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown setting")
    problem = validate_value(key, body.value)
    if problem:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, {"code": "invalid_setting", "message": problem}
        )
    previous = get_config(db, key)
    set_config(db, key, body.value)
    audit.record(
        db,
        "config_changed",
        "engine_config",
        key,
        actor=user.username,
        actor_role=user.role,
        detail={"previous": previous, "new": body.value},
    )
    db.commit()
    return {"key": key, "value": body.value}


def _busy(exc: "jobs.JobConflict") -> JSONResponse:
    return JSONResponse(
        jsonable_encoder(
            {
                "detail": {
                    "code": "job_active",
                    "message": "Another engine operation is already queued or running.",
                    "job": jobs.job_out(exc.job) if exc.job else None,
                }
            }
        ),
        status_code=status.HTTP_409_CONFLICT,
    )


def _queued(db: Session, kind: str, user: User, params: dict | None = None) -> JSONResponse:
    """Queues an engine job for the worker and answers 202 with it at once; 409 with the
    active job if another engine operation is already queued or running."""
    try:
        job = jobs.submit(db, kind, user, params)
    except jobs.JobConflict as exc:
        return JSONResponse(
            jsonable_encoder(
                {
                    "detail": {
                        "code": "job_active",
                        "message": "Another engine operation is already queued or running.",
                        "job": jobs.job_out(exc.job) if exc.job else None,
                    }
                }
            ),
            status_code=status.HTTP_409_CONFLICT,
        )
    return JSONResponse(
        jsonable_encoder({"job": jobs.job_out(job)}), status_code=status.HTTP_202_ACCEPTED
    )


@router.post("/admin/cycle", status_code=status.HTTP_202_ACCEPTED)
def run_cycle(
    retrain: bool = False, user: User = Depends(admin), db: Session = Depends(get_db)
) -> JSONResponse:
    """Queues a cycle (catch the synthetic population up to today, refresh features,
    regenerate recommendations and drafts, publish summaries). The worker runs it; poll
    GET /api/admin/jobs/{id}. Its `result` is what this endpoint used to return:
    {cycle_id, as_of_date, stats}."""
    return _queued(db, "cycle", user, {"retrain": retrain})


@router.get("/admin/jobs")
def list_jobs(_: User = Depends(admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(Job).order_by(Job.id.desc()).limit(20))
    return [jobs.job_out(j) for j in rows]


@router.get("/admin/jobs/{job_id}")
def get_job(job_id: int, _: User = Depends(admin), db: Session = Depends(get_db)) -> dict:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return jobs.job_out(job)


@router.get("/admin/cycles")
def list_cycles(_: User = Depends(admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(EngineCycle).order_by(EngineCycle.id.desc()).limit(20))
    return [
        {
            "id": c.id,
            "as_of_date": c.as_of_date,
            "started_ts": c.started_ts,
            "finished_ts": c.finished_ts,
            "stats": c.stats,
        }
        for c in rows
    ]


@router.get("/admin/models")
def list_models(_: User = Depends(model_readers), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ModelVersion).order_by(ModelVersion.name, ModelVersion.version.desc()))
    return [
        {
            "name": m.name,
            "version": m.version,
            "algorithm": m.algorithm,
            "trained_ts": m.trained_ts,
            "as_of_date": m.as_of_date,
            "is_active": m.is_active,
            "metrics": m.metrics,
            "features": m.features,
        }
        for m in rows
    ]


@router.post("/admin/play-out", status_code=status.HTTP_202_ACCEPTED)
def play_out(user: User = Depends(admin), db: Session = Depends(get_db)) -> JSONResponse:
    """Queues a play-out: simulated people respond now to what was sent to them, their own
    refills are caught up to today, and the engine runs again (the date is never moved).
    The job's `result` is {as_of_date, responses, cycle_id, cycle}."""
    return _queued(db, "play_out", user)


@router.post("/admin/reset")
def reset_demo(
    request: Request,
    body: ResetBody | None = None,
    user: User = Depends(admin),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Throw the demo data away and rebuild it. Registered accounts, invitations and the
    security audit trail are kept; every session ends (account ids may change), and the
    caller gets a new one."""
    body = body or ResetBody()
    actor_email = user.email
    try:
        lock_id = jobs.acquire(db, "reset", user)
    except jobs.JobConflict as exc:
        return _busy(exc)
    try:
        generate(db, GenConfig(n_patients=body.patients, n_hcps=body.hcps))
        result = cycle.run(db, retrain=True)
        db.commit()
    except BaseException:
        db.rollback()
        jobs.release(db, lock_id, ok=False)
        raise
    # The rebuild emptied the job table, lock row included: nothing is left to release.
    snapshots.publish_safely(db, result.cycle.id)
    audit.record(
        db,
        "demo_reset",
        "engine_cycle",
        result.cycle.id,
        actor=user.username,
        actor_role=user.role,
        detail={"patients": body.patients, "hcps": body.hcps},
    )
    # The rebuild re-creates every account row, so the caller's session is re-issued.
    me = SqlUserRepository(db).get_by_email(actor_email)
    token = sessions.issue(db, me, request.headers.get("user-agent"))
    db.commit()
    response = JSONResponse(
        jsonable_encoder(
            {
                "as_of_date": result.cycle.as_of_date,
                "cycle_id": result.cycle.id,
                "stats": result.stats,
                "user": account_out(me, db),
            }
        )
    )
    set_session_cookie(response, token)
    return response


@router.post("/admin/bulk-send")
def bulk_send(
    body: BulkSendBody | None = None, user: User = Depends(admin), db: Session = Depends(get_db)
) -> dict:
    """Demo shortcut: approve and send the highest-priority ready recommendations in one go.

    Stands in for a team working its queues for a week. Each one still goes through the
    same gate re-check and audit trail as a manual approval.
    """
    body = body or BulkSendBody()
    try:
        lock_id = jobs.acquire(db, "bulk_send", user)
    except jobs.JobConflict as exc:
        return _busy(exc)
    try:
        result = _bulk_send(db, body, user)
    except BaseException:
        db.rollback()
        jobs.release(db, lock_id, ok=False)
        raise
    jobs.release(db, lock_id, ok=True, result=result)
    return result


def _bulk_send(db: Session, body: BulkSendBody, user: User) -> dict:
    rows = db.scalars(
        select(Nba)
        .where(
            Nba.status == NbaStatus.READY_FOR_REVIEW,
            # Scheduled for a later day: left for that day.
            Nba.scheduled_for <= clock.get_today(db),
        )
        .order_by(Nba.priority.desc(), Nba.id)
        .limit(body.limit)
    ).all()
    sent = blocked = 0
    for nba in rows:
        nba.status = NbaStatus.APPROVED
        nba.reviewed_by_user_id, nba.reviewed_ts = user.id, utcnow()
        audit.record(
            db,
            "nba_approved",
            "nba",
            nba.id,
            nba_id=nba.id,
            actor=user.username,
            actor_role=user.role,
            reason="Bulk approval (demo shortcut)",
        )
        try:
            delivery.send(db, nba, user)
            sent += 1
        except delivery.SendBlocked:
            blocked += 1
    db.commit()
    return {"considered": len(rows), "sent": sent, "blocked_at_send": blocked}
