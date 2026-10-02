"""Session tokens.

A session is a signed token carrying the user id and the account's token version. The
role is never read from the token: every request loads the account and takes the role
from the database, so editing a token cannot change what a user may do.
"""

from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import User
from app.models.enums import AccountStatus

SESSION, VERIFY = "session", "verify"


def _encode(user: User, kind: str, minutes: int) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user.id),
        "typ": kind,
        "ver": user.token_version,
        "exp": datetime.now(UTC) + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _decode(token: str, kind: str) -> dict | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    return payload if payload.get("typ") == kind else None


def _load(db: Session, payload: dict | None) -> User | None:
    if payload is None:
        return None
    try:
        user = db.get(User, int(payload["sub"]))
    except (KeyError, ValueError):
        return None
    if user is None or payload.get("ver") != user.token_version:
        return None
    return user


class SessionService:
    """Issue, resolve and revoke sessions. Swap this class for a server-side session store
    or an identity provider's token validation; callers only use these three methods."""

    def issue(self, user: User) -> str:
        return _encode(user, SESSION, get_settings().access_token_minutes)

    def resolve(self, db: Session, token: str) -> User | None:
        """The active, verified account behind a session token, or None."""
        user = _load(db, _decode(token, SESSION))
        if user is None or not user.verified or user.status != AccountStatus.ACTIVE:
            return None
        return user

    def revoke(self, user: User) -> None:
        """Ends every session of this account (logout, disable)."""
        user.token_version += 1

    # A verification token proves "this browser just registered or signed in with the right
    # password" while the account is still unverified. It can only be used to enter or
    # re-request the one-time code; it is not a session.
    def issue_verification(self, user: User) -> str:
        return _encode(user, VERIFY, get_settings().verification_token_minutes)

    def resolve_verification(self, db: Session, token: str) -> User | None:
        return _load(db, _decode(token, VERIFY))


sessions = SessionService()
