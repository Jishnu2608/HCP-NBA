"""AI operations over HTTP: read the current result, request a generation.

Subject-agnostic: `GET /api/ai/{operation}/current` and `POST /api/ai/{operation}/generations`
serve every registered operation, whatever its subject type (a record id where the operation's
subject needs one, none for the reader's own queue or aggregates). The operation decides the
subject type; a request can never choose another. The patient routes are kept for the Case
Brief and answer exactly as the generic ones do.

Thin by design: every decision (permission, record scope, mode, provider, checks, audit) is
made in `app.ai.orchestrator`, which the routes only translate. Access is decided on the server
from the session's account; the operation's own permission is checked there too, so an
account without it gets 403 and a record outside its scope gets 404, as everywhere else.
"""

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.ai import orchestrator
from app.ai.contracts import SubjectType
from app.ai.registry import UnknownOperation, resolve
from app.api.deps import get_current_user
from app.core.db import get_db
from app.core.permissions import can
from app.models import User
from app.services import patients
from app.services.errors import Forbidden, NotFound

router = APIRouter(prefix="/api/ai", tags=["ai"])

OPERATION = Path(pattern=r"^[a-z_]{1,48}$")
PATIENT = Path(pattern=r"^[A-Za-z0-9_-]{1,32}$")
SUBJECT = r"^[A-Za-z0-9_-]{1,32}$"


def _unknown() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def _current_figures(db: Session, user: User, operation: str, patient_id: str) -> None:
    """Brings a real patient's adherence figures up to today before the AI layer reads them,
    exactly as opening Patient 360 does. This is the screen's decision: the context layer
    itself never writes, and reports figures that are not current as stale."""
    try:
        spec = resolve(operation)
    except UnknownOperation:
        raise _unknown() from None
    if SubjectType(spec.subject_type) is not SubjectType.PATIENT:
        return  # only Patient 360 figures are brought up to date by a screen
    if not can(user, spec.permission):
        return  # the orchestrator answers 403, audited
    try:
        if patients.refresh_if_stale(db, user, patient_id):
            db.commit()
    except (Forbidden, NotFound):
        db.rollback()  # the orchestrator answers with the same error, audited


@router.get("/{operation}/patients/{patient_id}")
def current_result(
    operation: str = OPERATION,
    patient_id: str = PATIENT,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The result to show now (template, or checked model wording), the reader's notes with
    the untrusted label, the cited sources and the latest generation's status."""
    _current_figures(db, user, operation, patient_id)
    try:
        view = orchestrator.current(db, user, operation, patient_id)
    except UnknownOperation:
        raise _unknown() from None
    except orchestrator.GenerationRefused as exc:  # AI tables missing: 503, nothing else breaks
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None
    db.commit()
    return jsonable_encoder(view)


@router.post("/{operation}/patients/{patient_id}/generations")
def request_generation(
    operation: str = OPERATION,
    patient_id: str = PATIENT,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Queues a generation (202), or returns the one already queued or running (200)."""
    _current_figures(db, user, operation, patient_id)
    try:
        gen, created = orchestrator.submit(db, user, operation, patient_id)
    except UnknownOperation:
        raise _unknown() from None
    except orchestrator.GenerationRefused as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None
    body = {"generation_id": gen.id, "status": gen.status, "created": created}
    return JSONResponse(body, status_code=202 if created else 200)


# --- Subject-agnostic routes (every registered operation) ---------------------------------------


def _translate(call):
    try:
        return call()
    except UnknownOperation:
        raise _unknown() from None
    except orchestrator.GenerationRefused as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None


@router.get("/{operation}/current")
def current_any(
    operation: str = OPERATION,
    subject_id: str | None = Query(None, pattern=SUBJECT),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The result to show now for this operation's subject (template, or checked model
    wording), its cited sources, reader notes and the latest generation's status."""
    if subject_id:
        _current_figures(db, user, operation, subject_id)
    view = _translate(lambda: orchestrator.current(db, user, operation, subject_id))
    db.commit()
    return jsonable_encoder(view)


@router.post("/{operation}/generations")
def request_any(
    operation: str = OPERATION,
    subject_id: str | None = Body(None, embed=True, pattern=SUBJECT),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> JSONResponse:
    """Queues a generation (202), or returns the one already queued or running (200)."""
    if subject_id:
        _current_figures(db, user, operation, subject_id)
    gen, created = _translate(lambda: orchestrator.submit(db, user, operation, subject_id))
    body = {"generation_id": gen.id, "status": gen.status, "created": created}
    return JSONResponse(body, status_code=202 if created else 200)
