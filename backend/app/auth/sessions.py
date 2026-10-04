"""Sessions.

A session is a row in `user_session`. The browser holds a random token in an HttpOnly
cookie (JavaScript cannot read it); only the token's hash is stored. Every request loads
the account behind the session and takes the role from the database, so nothing the
browser sends can change what a user may do.

Sessions end on sign-out (this browser only), after an idle period, at an absolute
lifetime, and all at once when the account is disabled, its password is rotated or the
demo is reset.

A *verification* token is different: it proves "this browser just registered or signed in
with the right password" while the email is still unverified, and can only be used to
enter or re-request the one-time code. It is a short-lived signed token, also kept in an
HttpOnly cookie. A *decoy* verification token is handed out when someone registers with an
email that already has an account, so the response looks the same; it can never succeed.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import new_token, token_hash
from app.models import User, UserSession
from app.models.enums import AccountStatus
from app.models.tables import utcnow

SESSION_COOKIE = "nba_session"
VERIFY_COOKIE = "nba_verify"
VERIFY = "verify"
# last_seen_at is written at most this often, so reads do not all become writes.
TOUCH_EVERY = timedelta(seconds=60)


@dataclass
class Decoy:
    """Stands in for an account during a sign-up with an email that is already registered."""

    email: str
    attempts: int
    issued: datetime


class SessionService:
    """Issue, resolve and revoke sessions. Swap this class for an identity provider's token
    validation; callers only use these methods."""

    def __init__(self, now=utcnow) -> None:
        self._now = now

    # --- sessions ----------------------------------------------------------------

    def issue(self, db: Session, user: User, user_agent: str | None = None) -> str:
        settings = get_settings()
        token, now = new_token(), self._now()
        db.add(
            UserSession(
                token_hash=token_hash(token),
                user_id=user.id,
                created_at=now,
                last_seen_at=now,
                expires_at=now + timedelta(minutes=settings.access_token_minutes),
                user_agent=(user_agent or "")[:160] or None,
            )
        )
        db.flush()
        return token

    def resolve(self, db: Session, token: str | None) -> User | None:
        """The active, verified account behind a session cookie, or None."""
        if not token:
            return None
        row = db.scalar(select(UserSession).where(UserSession.token_hash == token_hash(token)))
        if row is None:
            return None
        now = self._now()
        idle = timedelta(minutes=get_settings().session_idle_minutes)
        if now >= row.expires_at or now >= row.last_seen_at + idle:
            db.delete(row)
            db.commit()
            return None
        user = db.get(User, row.user_id)
        if user is None or not user.verified or user.status != AccountStatus.ACTIVE:
            return None
        if now - row.last_seen_at >= TOUCH_EVERY:
            row.last_seen_at = now
            db.commit()
        return user

    def revoke(self, db: Session, token: str | None) -> None:
        """Ends one session (sign-out in this browser)."""
        if token:
            db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))

    def revoke_all(self, db: Session, user: User) -> None:
        """Ends every session of the account (disable, password rotation)."""
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))

    def revoke_everyone(self, db: Session) -> None:
        """Ends all sessions (demo reset: account ids may be reassigned)."""
        db.execute(delete(UserSession))

    def purge(self, db: Session) -> None:
        db.execute(delete(UserSession).where(UserSession.expires_at <= self._now()))

    # --- verification tokens -------------------------------------------------------

    def _encode(self, payload: dict) -> str:
        settings = get_settings()
        payload = {
            **payload,
            "typ": VERIFY,
            "exp": datetime.now(UTC) + timedelta(minutes=settings.verification_token_minutes),
        }
        return jwt.encode(payload, settings.secret("jwt_secret"), algorithm=settings.jwt_algorithm)

    def issue_verification(self, user: User) -> str:
        return self._encode({"sub": str(user.id), "ver": user.token_version})

    def issue_decoy(self, email: str, attempts: int = 0) -> str:
        issued = int(datetime.now(UTC).timestamp())
        return self._encode({"sub": "decoy", "email": email, "att": attempts, "iat": issued})

    def resolve_verification(self, db: Session, token: str | None) -> User | Decoy | None:
        if not token:
            return None
        settings = get_settings()
        try:
            payload = jwt.decode(
                token, settings.secret("jwt_secret"), algorithms=[settings.jwt_algorithm]
            )
        except jwt.PyJWTError:
            return None
        if payload.get("typ") != VERIFY:
            return None
        if payload.get("sub") == "decoy":
            return Decoy(
                email=str(payload.get("email", "")),
                attempts=int(payload.get("att", 0)),
                issued=datetime.fromtimestamp(int(payload.get("iat", 0)), UTC).replace(tzinfo=None),
            )
        try:
            user = db.get(User, int(payload["sub"]))
        except (KeyError, ValueError):
            return None
        if user is None or payload.get("ver") != user.token_version:
            return None
        return user


sessions = SessionService()
