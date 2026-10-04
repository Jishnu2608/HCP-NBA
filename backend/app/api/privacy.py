"""Legal documents (public), the signed-in person's privacy controls, and the privacy
request queue for those who handle it.

Every action here is on the caller's own account, except the queue, which needs
`privacy:manage`. Nothing a person submits is carried out automatically except the copy of
their own data.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.api.schemas import StrictBody
from app.core import jurisdiction
from app.core.db import get_db
from app.core.permissions import Permission
from app.legal import consents, export, registry, requests
from app.models import PrivacyRequest, User

legal = APIRouter(prefix="/api/legal", tags=["legal"])
router = APIRouter(prefix="/api/privacy", tags=["privacy"])
admin = APIRouter(prefix="/api/admin/privacy-requests", tags=["privacy"])

self_service = require_permission(Permission.PRIVACY_SELF)
handler = require_permission(Permission.PRIVACY_MANAGE)


# --- Public ----------------------------------------------------------------------------------


@legal.get("/documents")
def documents() -> list[dict]:
    return registry.summary()


@legal.get("/jurisdictions")
def jurisdictions() -> dict:
    return jurisdiction.options()


@legal.get("/documents/{kind}")
def document(kind: str, version: str | None = Query(None, max_length=16)) -> dict:
    """The current version, or the exact version someone accepted."""
    return registry.load(kind, version)


# --- Own account -------------------------------------------------------------------------------


class AcceptBody(StrictBody):
    kinds: list[Literal["terms", "privacy_ack", "health_data"]] = Field(min_length=1, max_length=3)


class WithdrawBody(StrictBody):
    kind: Literal["health_data"]


class RequestBody(StrictBody):
    type: Literal[
        "access", "rectification", "erasure", "restriction", "portability", "objection",
        "consent_withdrawal", "other",
    ]  # fmt: skip
    details: str | None = Field(default=None, max_length=2000)


@router.get("/status")
def status(user: User = Depends(self_service), db: Session = Depends(get_db)) -> dict:
    return {
        **consents.status(db, user),
        "documents": registry.summary(),
        "jurisdiction": jurisdiction.code(user.country, user.region),
        "request_types": requests.TYPES,
    }


@router.post("/accept")
def accept(
    body: AcceptBody, user: User = Depends(self_service), db: Session = Depends(get_db)
) -> dict:
    """Accept the current version of the listed documents / consents (the re-acceptance
    screen and Data & privacy)."""
    source = "reacceptance" if consents.pending(db, user) else "settings"
    left = consents.accept(db, user, list(body.kinds), source)
    db.commit()
    return {"pending": left}


@router.post("/withdraw")
def withdraw(
    body: WithdrawBody, user: User = Depends(self_service), db: Session = Depends(get_db)
) -> dict:
    consents.withdraw(db, user, body.kind)
    db.commit()
    return consents.status(db, user)


@router.get("/history")
def history(user: User = Depends(self_service), db: Session = Depends(get_db)) -> list[dict]:
    return consents.history(db, user)


@router.get("/requests")
def my_requests(user: User = Depends(self_service), db: Session = Depends(get_db)) -> list:
    return requests.own(db, user)


@router.post("/requests", status_code=201)
def submit_request(
    body: RequestBody, user: User = Depends(self_service), db: Session = Depends(get_db)
) -> dict:
    row = requests.create_request(db, user, body.type, body.details)
    db.commit()
    return requests.out(row)


@router.get("/export")
def download_export(user: User = Depends(self_service), db: Session = Depends(get_db)):
    data = export.build(db, user)
    db.commit()
    return JSONResponse(
        jsonable_encoder(data),
        headers={"Content-Disposition": 'attachment; filename="my-data.json"'},
    )


# --- Handling requests ---------------------------------------------------------------------------


class UpdateBody(StrictBody):
    status: Literal["in_review", "completed", "rejected"]
    resolution: str | None = Field(default=None, max_length=2000)


@admin.get("")
def queue(
    status: Literal["submitted", "in_review", "completed", "rejected"] | None = None,
    _: User = Depends(handler),
    db: Session = Depends(get_db),
) -> dict:
    query = select(PrivacyRequest).order_by(PrivacyRequest.id.desc())
    if status:
        query = query.where(PrivacyRequest.status == status)
    rows = db.scalars(query).all()
    items = [requests.out(r, with_requester=db.get(User, r.user_id)) for r in rows]
    counts: dict[str, int] = {}
    for r in db.scalars(select(PrivacyRequest.status)):
        counts[r] = counts.get(r, 0) + 1
    return {"items": items, "counts": counts}


@admin.patch("/{request_id}")
def update_request(
    request_id: int,
    body: UpdateBody,
    user: User = Depends(handler),
    db: Session = Depends(get_db),
) -> dict:
    row = requests.update(db, user, request_id, body.status, body.resolution)
    db.commit()
    return requests.out(row, with_requester=db.get(User, row.user_id))
