"""The authentication flow: register (patients), verify, sign in, sign out.

Depends only on the interfaces in this package (UserRepository, OTPService, SessionService,
AssignmentService, InvitationService), so each can be replaced independently.

Every function returns an `AuthResult`: the JSON body for the browser plus, separately, any
token that belongs in an HttpOnly cookie. Tokens never appear in a response body.
"""

import logging
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth import invitations
from app.auth.errors import AuthError, invalid_credentials
from app.auth.otp import Issued, get_sender, otp_service
from app.auth.provisioning import assignments
from app.auth.repository import SqlUserRepository, normalize_email
from app.auth.sessions import Decoy, sessions
from app.core import age
from app.core.config import get_settings
from app.core.permissions import PUBLIC_SIGNUP_ROLE, ROLE_HOME, permissions_for
from app.core.security import fingerprint, hash_password, verify_password
from app.mail.templates import account_exists_email
from app.mail.transport import get_mailer
from app.models import User
from app.models.enums import AccountSource, AccountStatus, Role
from app.models.tables import utcnow

log = logging.getLogger("nba.auth")

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN = 10
# Compared against when the email is unknown, so both failure paths cost the same.
_DUMMY_HASH = hash_password("not-a-real-password")
ANONYMOUS = "anonymous"


@dataclass
class AuthResult:
    body: dict
    session_token: str | None = None
    verify_token: str | None = None
    # True when the verification cookie should be removed (verification finished).
    clear_verify: bool = False
    extra: dict = field(default_factory=dict)


def account_out(user: User) -> dict:
    """What the client may know about the signed-in account. Permissions come from the
    server-side role map; the client never supplies or decides them. The date of birth is
    never included."""
    return {
        "id": user.id,
        "name": user.display_name,
        "email": user.email,
        "role": user.role,
        "permissions": sorted(permissions_for(user.role)),
        "home": ROLE_HOME.get(user.role, "/"),
        "hcp_id": user.hcp_id,
        "patient_id": user.patient_id,
        "email_verified": user.verified,
        "professionally_verified": user.professionally_verified,
        "verification_source": user.verification_source,
    }


def _signed_in(db: Session, user: User, user_agent: str | None) -> AuthResult:
    return AuthResult(
        body={"user": account_out(user)},
        session_token=sessions.issue(db, user, user_agent),
        clear_verify=True,
    )


def _challenge_body(email: str, issued: Issued) -> dict:
    body = {
        "email": email,
        "delivery": issued.delivery,
        "expires_in": issued.expires_in,
        "resend_in": issued.resend_in,
    }
    if issued.dev_code is not None:
        body["dev_otp"] = issued.dev_code
    return body


def issue_or_report(db: Session, user: User) -> AuthResult:
    """Issues a code for a pending account. If the email cannot be sent, the account stays
    pending and the caller is told so (delivery "failed"), so the verify screen can explain
    and offer to send again; nothing about the failure beyond that reaches the client."""
    token = sessions.issue_verification(user)
    try:
        issued = otp_service.issue(db, user, respect_cooldown=False)
    except AuthError as exc:
        if exc.code != "otp_delivery_failed":
            raise
        body = {"email": user.email, "delivery": "failed", "expires_in": 0, "resend_in": 0}
        return AuthResult(body=body, verify_token=token)
    return AuthResult(body=_challenge_body(user.email, issued), verify_token=token)


def validate_password(password: str, confirm: str) -> None:
    if password != confirm:
        raise AuthError(422, "password_mismatch", "The two passwords do not match.")
    if (
        len(password) < PASSWORD_MIN
        or not re.search(r"[A-Za-z]", password)
        or not re.search(r"\d", password)
    ):
        raise AuthError(
            422,
            "weak_password",
            f"Use at least {PASSWORD_MIN} characters with at least one letter and one number.",
        )


def validate_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise AuthError(422, "invalid_name", "Enter your name.")
    return name


def validate_email(email: str) -> str:
    email = normalize_email(email)
    if not EMAIL.match(email):
        raise AuthError(422, "invalid_email", "Enter a valid email address.")
    return email


# --- Patient sign-up -----------------------------------------------------------------


def _decoy(db: Session, email: str) -> AuthResult:
    """Sign-up with an email that already has an account. The response has the same shape
    as a real sign-up, so the form cannot be used to find out which emails are registered.
    The owner is told by email; no code is issued and nothing can be verified."""
    mailer = get_mailer()
    if mailer is not None:
        try:
            mailer.deliver(account_exists_email(email))
        except Exception as exc:  # noqa: BLE001 - the response must not differ on failure
            log.error("Could not send sign-up notice: %s", type(exc).__name__)
    settings = get_settings()
    issued = Issued(
        delivery=get_sender().channel,
        expires_in=settings.otp_ttl_minutes * 60,
        resend_in=settings.otp_resend_seconds,
        # Local development only: a code is shown for real sign-ups, so one is shown here
        # too. It will never verify.
        dev_code=_random_code() if settings.show_development_codes else None,
    )
    audit.record(
        db, "signup_existing_email", "auth", fingerprint(email),
        actor=ANONYMOUS, actor_role=ANONYMOUS,
    )  # fmt: skip
    return AuthResult(body=_challenge_body(email, issued), verify_token=sessions.issue_decoy(email))


def _random_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def signup(
    db: Session, *, name: str, email: str, date_of_birth: str, password: str, confirm: str
) -> AuthResult:
    """Public registration. Always creates a patient: professional roles exist only through
    an invitation, and no role is accepted from the request."""
    repo = SqlUserRepository(db)
    name = validate_name(name)
    email = validate_email(email)
    dob = age.parse_dob(date_of_birth)
    validate_password(password, confirm)
    get_sender()  # no way to deliver a code at all: refuse before anything is stored

    existing = repo.get_by_email(email)
    if existing is not None:
        reusable = (
            not existing.verified
            and existing.source == AccountSource.SIGNUP
            and existing.status == AccountStatus.PENDING
        )
        if not reusable:
            return _decoy(db, email)
        # An unfinished sign-up for this address: start it again with the new details.
        # Nothing is activated until the code sent to the inbox is entered.
        existing.display_name = name
        existing.date_of_birth = dob
        existing.password_hash = hash_password(password)
        user = existing
    else:
        user = repo.create_pending(
            name=name,
            email=email,
            password_hash=hash_password(password),
            role=PUBLIC_SIGNUP_ROLE,
            source=AccountSource.SIGNUP,
        )
        user.date_of_birth = dob
    db.flush()
    result = issue_or_report(db, user)
    audit.record(
        db,
        "account_created",
        "user",
        user.id,
        actor=user.username,
        actor_role=user.role,
        detail={"delivery": result.body["delivery"], "source": user.source},
    )
    return result


# --- One-time code ---------------------------------------------------------------------


def _pending(db: Session, verify_token: str | None) -> User | Decoy:
    found = sessions.resolve_verification(db, verify_token)
    if found is None:
        raise AuthError(401, "verification_expired", "Verification has expired. Sign in again.")
    if isinstance(found, Decoy):
        return found
    if found.status == AccountStatus.DISABLED:
        raise AuthError(403, "account_disabled", "This account has been disabled.")
    if found.verified:
        raise AuthError(409, "already_verified", "This account is already verified. Sign in.")
    return found


def _decoy_attempt(decoy: Decoy) -> None:
    limit = get_settings().otp_max_attempts
    attempts = decoy.attempts + 1
    left = limit - attempts
    if decoy.attempts >= limit:
        raise AuthError(400, "otp_missing", "No active code. Request a new one.")
    if left <= 0:
        raise AuthError(
            429, "otp_locked", "Too many attempts. Request a new code.",
            verify_token=sessions.issue_decoy(decoy.email, attempts),
        )  # fmt: skip
    raise AuthError(
        400, "otp_incorrect", f"Incorrect code. {left} attempts left.",
        verify_token=sessions.issue_decoy(decoy.email, attempts),
    )  # fmt: skip


def verify_otp(db: Session, verify_token: str | None, code: str, user_agent: str | None):
    pending = _pending(db, verify_token)
    if isinstance(pending, Decoy):
        _decoy_attempt(pending)
    user = pending
    try:
        otp_service.verify(db, user, code)
    except AuthError as exc:
        audit.record(
            db, "otp_failed", "user", user.id, actor=user.username, actor_role=user.role,
            detail={"code": exc.code},
        )  # fmt: skip
        raise
    # A professional account is only completed through its invitation. This re-checks the
    # invitation (still pending, not expired, inviter still authorised) and records lineage.
    invitation = invitations.complete_for(db, user)
    repo = SqlUserRepository(db)
    repo.activate(user)
    assigned = assignments.auto_provision(db, user)
    repo.record_login(user)
    audit.record(
        db,
        "account_verified",
        "user",
        user.id,
        actor=user.username,
        actor_role=user.role,
        detail={
            "assigned": assigned,
            "invitation": invitation.id if invitation else None,
            "professionally_verified": user.professionally_verified,
        },
    )
    return _signed_in(db, user, user_agent)


def resend_otp(db: Session, verify_token: str | None) -> AuthResult:
    pending = _pending(db, verify_token)
    settings = get_settings()
    if isinstance(pending, Decoy):
        wait = pending.issued + timedelta(seconds=settings.otp_resend_seconds) - utcnow()
        if wait.total_seconds() > 0:
            seconds = int(wait.total_seconds()) + 1
            raise AuthError(
                429, "otp_resend_wait", f"Wait {seconds} seconds before requesting another code."
            )
        issued = Issued(
            delivery=get_sender().channel,
            expires_in=settings.otp_ttl_minutes * 60,
            resend_in=settings.otp_resend_seconds,
            dev_code=_random_code() if settings.show_development_codes else None,
        )
        return AuthResult(
            body=_challenge_body(pending.email, issued),
            verify_token=sessions.issue_decoy(pending.email),
        )
    issued = otp_service.issue(db, pending)
    audit.record(
        db, "otp_resent", "user", pending.id, actor=pending.username, actor_role=pending.role
    )
    return AuthResult(
        body=_challenge_body(pending.email, issued),
        verify_token=sessions.issue_verification(pending),
    )


# --- Sign-in / sign-out -------------------------------------------------------------------


def _login_failed(db: Session, email: str, reason: str) -> None:
    audit.record(
        db, "login_failed", "auth", fingerprint(email), actor=ANONYMOUS, actor_role=ANONYMOUS,
        detail={"reason": reason},
    )  # fmt: skip


def login(db: Session, email: str, password: str, user_agent: str | None) -> AuthResult:
    repo = SqlUserRepository(db)
    email = normalize_email(email)
    user = repo.get_by_email(email)
    if user is None:
        verify_password(password, _DUMMY_HASH)
        _login_failed(db, email, "invalid_credentials")
        raise invalid_credentials()
    if not verify_password(password, user.password_hash):
        _login_failed(db, email, "invalid_credentials")
        raise invalid_credentials()
    if user.status == AccountStatus.DISABLED:
        _login_failed(db, email, "account_disabled")
        raise AuthError(403, "account_disabled", "This account has been disabled.")
    if not user.verified:
        # Right password, unverified account: hand back a way to finish verification,
        # never a session. A code is issued only if none is currently active.
        status = otp_service.status(db, user)
        if status["expires_in"] == 0:
            result = issue_or_report(db, user)
            extra, token = result.body, result.verify_token
        else:
            # A code is already active. Say how it was delivered, never the code itself.
            extra = {"email": user.email, **status, "delivery": get_sender().channel}
            token = sessions.issue_verification(user)
        raise AuthError(
            403,
            "verification_required",
            "Verify your email to finish creating the account.",
            verify_token=token,
            **extra,
        )
    repo.record_login(user)
    audit.record(db, "login_succeeded", "user", user.id, actor=user.username, actor_role=user.role)
    return _signed_in(db, user, user_agent)


def logout(db: Session, user: User, session_token: str | None) -> None:
    sessions.revoke(db, session_token)
    audit.record(db, "logout", "user", user.id, actor=user.username, actor_role=user.role)


def ensure_system_admin(db: Session) -> User:
    """The fixed administrator account. Created if missing; never created through sign-up."""
    settings = get_settings()
    email = normalize_email(settings.admin_email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            username="admin",
            email=email,
            display_name="Administrator",
            password_hash=hash_password(settings.secret("admin_password")),
            role=Role.ADMIN,
            verified=True,
            status=AccountStatus.ACTIVE,
            source=AccountSource.SYSTEM,
        )
        db.add(user)
        db.flush()
    return user
