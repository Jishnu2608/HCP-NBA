"""AI operations over HTTP: read the current result, request a generation.

Thin by design: every decision (permission, record scope, mode, provider, checks, audit) is
made in `app.ai.orchestrator`, which the routes only translate. Access is decided on the server
from the session's account; the operation's own permission is checked there too, so an
account without it gets 403 and a record outside its scope gets 404, as everywhere else.
"""

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.ai import orchestrator
from app.ai.registry import UnknownOperation
from app.api.deps import get_current_user
from app.core.db import get_db
from app.models import User

router = APIRouter(prefix="/api/ai", tags=["ai"])

OPERATION = Path(pattern=r"^[a-z_]{1,48}$")
PATIENT = Path(pattern=r"^[A-Za-z0-9_-]{1,32}$")


def _unknown() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


@router.get("/{operation}/patients/{patient_id}")
def current_result(
    operation: str = OPERATION,
    patient_id: str = PATIENT,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """The result to show now (template, or checked model wording), the reader's notes with
    the untrusted label, the cited sources and the latest generation's status."""
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
    try:
        gen, created = orchestrator.submit(db, user, operation, patient_id)
    except UnknownOperation:
        raise _unknown() from None
    except orchestrator.GenerationRefused as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from None
    body = {"generation_id": gen.id, "status": gen.status, "created": created}
    return JSONResponse(body, status_code=202 if created else 200)
