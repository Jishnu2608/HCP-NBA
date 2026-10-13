"""Forgot password: email -> one-time code -> reset authorization -> new password.

Who may use it: every account holding `self:account:recover`, which is every role except the
administrator (core/permissions.py). The account and its role are always read from the
database, at every step: a role sent by the browser is never trusted. The administrator's
password is recovered only by the operator procedure (`python -m app.auth.rotate`); normal
administrator sign-in is unaffected.

The three steps, each a POST under /api/auth/password-reset:

  request   {email}           Starts a transaction. The browser gets a random transaction
                              token in an HttpOnly cookie. For an eligible account a code is
                              emailed; for anything else (unknown address, administrator,
                              disabled or unverified account) a transaction without an
                              account or code is stored, so every later answer looks the
                              same. The public answer never says which case applied.
  verify    {code}            Checks the code against this transaction only (keyed hash of
                              transaction, account and code). Five wrong tries lock it. On
                              success the code is spent and a separate reset authorization
                              is issued, also in an HttpOnly cookie, valid for 10 minutes.
  complete  {password, ...}   Needs both cookies of the same transaction. Re-checks the
                              account's current eligibility (a role changed to
                              administrator meanwhile stops it), applies the password policy,
                              hashes with the existing scrypt helper, ends every session,
                              spends the transaction, and emails a confirmation.

Only hashes of the code, the transaction token and the authorization are stored. No code,
token or password is logged or audited. A newer request supersedes earlier open
transactions of the same account.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import audit
from app.auth.errors import AuthError
from app.auth.repository import SqlUserRepository
from app.auth.service import ANONYMOUS, validate_email, validate_password
from app.auth.sessions import sessions
from app.core.config import get_settings
from app.core.permissions import Permission, can
from app.core.security import derived_key, fingerprint, hash_password, new_token, token_hash
from app.core.security import verify_password as password_matches
from app.mail.templates import password_changed_email, reset_code_email
from app.mail.transport import get_mailer
from app.models import PasswordReset, User
from app.models.enums import AccountStatus
from app.models.tables import utcnow

log = logging.getLogger("nba.recovery")

TRANSACTION_COOKIE = "nba_reset"
AUTHORIZATION_COOKIE = "nba_reset_auth"
COOKIE_PATH = "/api/auth/password-reset"
AUTHORIZATION_MINUTES = 10
# A transaction may be resent within this time from its start, never after.
TRANSACTION_MINUTES = 60
GENERIC = (
    "If this account is eligible for password reset, a verification code is on its way to "
    "that address."
)
EXPIRED = "This reset is no longer valid. Start again."


def eligible(user: User | None) -> bool:
    """The only eligibility rule, applied at every step against the database row."""
    return (
        user is not None
        and user.status == AccountStatus.ACTIVE
        and user.verified
        and can(user, Permission.SELF_ACCOUNT_RECOVERY)
    )


def _code_hash(row: PasswordReset, user_id: int, code: str) -> str:
    message = f"{row.id}:{user_id}:{code}".encode()
    return hmac.new(derived_key("password_reset"), message, hashlib.sha256).hexdigest()


def _random_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def _delivery() -> str:
    """How codes travel in this deployment. The same for every request, eligible or not."""
    if get_mailer() is not None:
        return "email"
    return "development" if get_settings().show_development_codes else "unavailable"


@dataclass
class Step:
    body: dict = field(default_factory=dict)
    transaction_token: str | None = None
    authorization_token: str | None = None
    clear_cookies: bool = False


def _public(dev_code: str | None) -> dict:
    settings = get_settings()
    body = {
        "message": GENERIC,
        "delivery": _delivery(),
        "expires_in": settings.otp_ttl_minutes * 60,
        "resend_in": settings.otp_resend_seconds,
    }
    if dev_code is not None:
        body["dev_otp"] = dev_code
    return body


def _issue_code(row: PasswordReset, user: User) -> tuple[str, str | None]:
    """Replaces the transaction's code and delivers it. Returns (outcome, development code).
    A failed send leaves the transaction without any usable code."""
    settings = get_settings()
    now = utcnow()
    row.code_hash = None
    row.attempts = 0
    row.expires_at = now + timedelta(minutes=settings.otp_ttl_minutes)
    row.resend_available_at = now + timedelta(seconds=settings.otp_resend_seconds)
    code = _random_code()
    mailer = get_mailer()
    if mailer is not None:
        try:
            mailer.deliver(
                reset_code_email(user.email, user.display_name, code, settings.otp_ttl_minutes)
            )
        except Exception as exc:  # noqa: BLE001 - any delivery failure is handled the same way
            log.error(
                "Could not email a password reset code to user %s: %s", user.id, type(exc).__name__
            )
            return "delivery_failed", None
        row.code_hash = _code_hash(row, user.id, code)
        return "issued", None
    if settings.show_development_codes:
        # Local run without a mail server: the code is shown on screen, never logged.
        row.code_hash = _code_hash(row, user.id, code)
        return "issued_development", code
    return "delivery_unavailable", None


def _blank_code() -> str | None:
    """A code shown in a local run for a transaction that can never succeed, so the screens
    look the same as for a real one."""
    return _random_code() if _delivery() == "development" else None


def _supersede(db: Session, user_id: int, keep: int) -> None:
    db.execute(
        update(PasswordReset)
        .where(
            PasswordReset.user_id == user_id,
            PasswordReset.id != keep,
            PasswordReset.consumed_at.is_(None),
            PasswordReset.invalidated_at.is_(None),
        )
        .values(invalidated_at=utcnow(), code_hash=None, authorization_hash=None)
    )


def _refuse(db: Session, row: PasswordReset, step: str) -> AuthError:
    """The account behind an open transaction is no longer eligible (for example it became an
    administrator): the transaction ends and nothing changes."""
    row.invalidated_at = utcnow()
    row.code_hash = None
    row.authorization_hash = None
    audit.record(
        db, "password_reset_refused", "user", row.user_id or 0, actor=ANONYMOUS,
        actor_role=ANONYMOUS, detail={"step": step, "reason": "not_eligible"},
    )  # fmt: skip
    return AuthError(409, "reset_unavailable", "This reset can't be completed. Start again.")


def _open(db: Session, transaction_token: str | None) -> PasswordReset:
    """The transaction behind the cookie, if it is still open."""
    if not transaction_token:
        raise AuthError(400, "reset_expired", EXPIRED)
    row = db.scalar(
        select(PasswordReset).where(PasswordReset.transaction_hash == token_hash(transaction_token))
    )
    if row is None or row.consumed_at is not None or row.invalidated_at is not None:
        raise AuthError(400, "reset_expired", EXPIRED)
    if utcnow() >= row.created_at + timedelta(minutes=TRANSACTION_MINUTES):
        row.invalidated_at = utcnow()
        raise AuthError(400, "reset_expired", EXPIRED)
    return row


# --- Steps -------------------------------------------------------------------------------


def request(db: Session, email: str) -> Step:
    email = validate_email(email)
    settings = get_settings()
    now = utcnow()
    user = SqlUserRepository(db).get_by_email(email)
    ok = eligible(user)
    token = new_token()
    row = PasswordReset(
        user_id=user.id if ok else None,
        transaction_hash=token_hash(token),
        expires_at=now + timedelta(minutes=settings.otp_ttl_minutes),
        resend_available_at=now + timedelta(seconds=settings.otp_resend_seconds),
        attempts=0,
        created_at=now,
    )
    db.add(row)
    db.flush()
    if ok:
        _supersede(db, user.id, keep=row.id)
        outcome, dev_code = _issue_code(row, user)
    else:
        outcome, dev_code = "not_eligible", _blank_code()
    audit.record(
        db, "password_reset_requested", "auth", fingerprint(email), actor=ANONYMOUS,
        actor_role=ANONYMOUS, detail={"outcome": outcome},
    )  # fmt: skip
    return Step(body=_public(dev_code), transaction_token=token)


def resend(db: Session, transaction_token: str | None) -> Step:
    row = _open(db, transaction_token)
    now = utcnow()
    if now < row.resend_available_at:
        wait = int((row.resend_available_at - now).total_seconds()) + 1
        raise AuthError(
            429, "otp_resend_wait", f"Wait {wait} seconds before requesting another code."
        )
    if row.verified_at is not None:
        raise AuthError(400, "reset_expired", EXPIRED)
    user = db.get(User, row.user_id) if row.user_id else None
    if row.user_id is not None and not eligible(user):
        raise _refuse(db, row, "resend")
    if user is not None:
        outcome, dev_code = _issue_code(row, user)
    else:
        settings = get_settings()
        row.expires_at = now + timedelta(minutes=settings.otp_ttl_minutes)
        row.resend_available_at = now + timedelta(seconds=settings.otp_resend_seconds)
        row.attempts = 0
        outcome, dev_code = "not_eligible", _blank_code()
    audit.record(
        db, "password_reset_resent", "auth", f"reset:{row.id}", actor=ANONYMOUS,
        actor_role=ANONYMOUS, detail={"outcome": outcome},
    )  # fmt: skip
    return Step(body=_public(dev_code))


def verify(db: Session, transaction_token: str | None, code: str) -> Step:
    settings = get_settings()
    row = _open(db, transaction_token)
    now = utcnow()
    if row.verified_at is not None:
        # The code was already used: a second verification is never possible.
        raise AuthError(400, "otp_missing", "This code was already used. Start again.")
    if now >= row.expires_at:
        row.code_hash = None
        raise AuthError(400, "otp_expired", "This code has expired. Request a new one.")
    if row.attempts >= settings.otp_max_attempts:
        raise AuthError(429, "otp_locked", "Too many attempts. Request a new code.")
    code = code.strip()
    matched = (
        row.user_id is not None
        and row.code_hash is not None
        and code.isdigit()
        and hmac.compare_digest(row.code_hash, _code_hash(row, row.user_id, code))
    )
    if not matched:
        row.attempts += 1
        left = settings.otp_max_attempts - row.attempts
        if left <= 0:
            row.code_hash = None
            raise AuthError(429, "otp_locked", "Too many attempts. Request a new code.")
        raise AuthError(400, "otp_incorrect", f"Incorrect code. {left} attempts left.")
    user = db.get(User, row.user_id)
    if not eligible(user):
        raise _refuse(db, row, "verify")
    authorization = new_token()
    row.code_hash = None
    row.verified_at = now
    row.authorization_hash = token_hash(authorization)
    row.authorization_expires_at = now + timedelta(minutes=AUTHORIZATION_MINUTES)
    audit.record(
        db, "password_reset_verified", "user", user.id, actor=user.username, actor_role=user.role
    )
    return Step(
        body={"verified": True, "expires_in": AUTHORIZATION_MINUTES * 60},
        authorization_token=authorization,
    )


def complete(
    db: Session,
    transaction_token: str | None,
    authorization_token: str | None,
    password: str,
    confirm: str,
) -> Step:
    row = _open(db, transaction_token)
    now = utcnow()
    authorized = (
        authorization_token is not None
        and row.verified_at is not None
        and row.authorization_hash is not None
        and row.authorization_expires_at is not None
        and now < row.authorization_expires_at
        and hmac.compare_digest(row.authorization_hash, token_hash(authorization_token))
    )
    if not authorized:
        raise AuthError(401, "reset_unauthorized", "Verify the code from your email first.")
    validate_password(password, confirm)
    user = db.get(User, row.user_id)
    if not eligible(user):
        raise _refuse(db, row, "complete")
    if password_matches(password, user.password_hash):
        raise AuthError(
            422, "password_reused", "Choose a password you have not used for this account."
        )
    user.password_hash = hash_password(password)
    # Outstanding verification tokens stop working, and every session ends.
    user.token_version += 1
    sessions.revoke_all(db, user)
    row.consumed_at = now
    row.authorization_hash = None
    _supersede(db, user.id, keep=row.id)
    audit.record(
        db, "password_reset_completed", "user", user.id, actor=user.username,
        actor_role=user.role, detail={"sessions_ended": True},
    )  # fmt: skip
    db.flush()
    mailer = get_mailer()
    if mailer is not None:
        try:
            mailer.deliver(password_changed_email(user.email, user.display_name, now))
        except Exception as exc:  # noqa: BLE001 - the password change itself has succeeded
            log.error(
                "Could not send password-changed notice to user %s: %s", user.id, type(exc).__name__
            )
    return Step(
        body={
            "reset": True,
            "message": "Your password was changed. Sign in with your new password.",
        },
        clear_cookies=True,
    )
