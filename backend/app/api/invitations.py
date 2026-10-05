"""Invitations for professional roles (and, bound to a record, clinic patients).

Inviter endpoints need a session and the onboarding authority for the role in question
(`can_invite`, checked in auth/invitations.py on every call, not only here). The two
recipient endpoints are public: the unguessable token in the body is the credential, it is
rate limited, and it only ever leads to a code sent to the invited email.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.auth import counted_send, run
from app.api.deps import get_current_user, require_permission
from app.api.schemas import StrictBody
from app.auth import invitations, service
from app.auth.errors import AuthError
from app.core import ratelimit
from app.core.db import get_db
from app.core.permissions import INVITE_PERMISSION, ROLE_DESCRIPTIONS, ROLE_LABELS, invitable_roles
from app.core.security import token_hash
from app.models import User

router = APIRouter(prefix="/api/invitations", tags=["invitations"])

inviter = require_permission(*INVITE_PERMISSION.values())


class InviteBody(StrictBody):
    email: str = Field(max_length=254)
    role: str = Field(max_length=32)


class TokenBody(StrictBody):
    token: str = Field(min_length=1, max_length=128)


class AcceptBody(StrictBody):
    """No email and no role: both come from the invitation, never from the browser."""

    token: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=128)
    date_of_birth: str = Field(max_length=10)
    password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)
    country: str = Field(max_length=2)
    region: str | None = Field(default=None, max_length=3)
    accept_terms: bool
    # Patient invitations only: the separate consent to process health information.
    consent_health_data: bool = False


def _created(db: Session, actor: User, result) -> dict:
    inv, dev_link = result
    body = {"invitation": invitations.out(db, inv, actor)}
    if dev_link:
        # Local run without a mail server only (never when email is configured).
        body["dev_link"] = dev_link
    return body


@router.get("/options")
def options(user: User = Depends(get_current_user)) -> list[dict]:
    """The roles this account may invite. Empty for accounts with no onboarding authority."""
    return [
        {"role": r, "label": ROLE_LABELS[r], "description": ROLE_DESCRIPTIONS[r]}
        for r in invitable_roles(user)
    ]


@router.get("")
def list_invitations(
    status: Literal["pending", "accepted", "expired", "revoked"] | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: User = Depends(inviter),
    db: Session = Depends(get_db),
) -> dict:
    """Administrators see every invitation; everyone else only the ones they sent."""
    result = invitations.listing(db, user, status, limit, offset)
    db.commit()  # expiries noticed while listing are recorded
    return result


@router.post("", status_code=201)
def create(
    body: InviteBody, request: Request, user: User = Depends(inviter), db: Session = Depends(get_db)
) -> dict:
    ratelimit.consume(db, "invite_create", f"user:{user.id}")
    try:
        result = _created(db, user, invitations.create(db, user, body.email, body.role))
    except AuthError:
        db.commit()  # an expiry noticed on the way is still recorded
        raise
    db.commit()
    return result


@router.post("/{invitation_id}/reissue")
def reissue(
    invitation_id: int, user: User = Depends(inviter), db: Session = Depends(get_db)
) -> dict:
    result = _created(db, user, invitations.reissue(db, user, invitation_id))
    db.commit()
    return result


@router.post("/{invitation_id}/revoke")
def revoke(
    invitation_id: int, user: User = Depends(inviter), db: Session = Depends(get_db)
) -> dict:
    inv = invitations.revoke(db, user, invitation_id)
    db.commit()
    return {"invitation": invitations.out(db, inv, user)}


@router.post("/lookup")
def lookup(body: TokenBody, request: Request, db: Session = Depends(get_db)) -> dict:
    """What the invitation is for. Public; the token is never echoed back."""
    ratelimit.consume(db, "invite_lookup_ip", ratelimit.client_ip(request))
    try:
        result = invitations.lookup(db, body.token)
    finally:
        db.commit()
    return result


@router.post("/accept", status_code=201)
def accept(body: AcceptBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Creates the pending account for the invited email and role, and emails the code.
    The invitation is accepted only when that code is entered."""
    ip = ratelimit.client_ip(request)
    ratelimit.consume(db, "invite_accept_ip", ip)

    def action():
        user = invitations.accept(
            db,
            body.token,
            name=body.name,
            date_of_birth=body.date_of_birth,
            password=body.password,
            confirm=body.confirm_password,
            country=body.country,
            region=body.region,
            accept_terms=body.accept_terms,
            consent_health_data=body.consent_health_data,
        )
        return service.issue_or_report(db, user)

    # One invitation is bound to one email, so its token stands in for the email here.
    return run(db, counted_send(db, ip, f"invite:{token_hash(body.token)}", action), status=201)
