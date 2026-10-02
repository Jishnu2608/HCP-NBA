"""The authentication flow: register, verify, sign in, sign out.

Depends only on the interfaces in this package (UserRepository, OTPService, SessionService,
AssignmentService), so each can be replaced independently.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.errors import AuthError, invalid_credentials
from app.auth.otp import Issued, get_sender, otp_service
from app.auth.provisioning import assignments
from app.auth.repository import SqlUserRepository, normalize_email
from app.auth.sessions import sessions
from app.core.config import get_settings
from app.core.permissions import ROLE_HOME, SIGNUP_ROLES, permissions_for
from app.core.security import hash_password, verify_password
from app.models import User
from app.models.enums import AccountSource, AccountStatus, Role

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN = 8
# Compared against when the email is unknown, so both failure paths cost the same.
_DUMMY_HASH = hash_password("not-a-real-password")


def account_out(user: User) -> dict:
    """What the client may know about the signed-in account. Permissions come from the
    server-side role map; the client never supplies or decides them."""
    return {
        "id": user.id,
        "name": user.display_name,
        "email": user.email,
        "role": user.role,
        "permissions": sorted(permissions_for(user.role)),
        "home": ROLE_HOME.get(user.role, "/"),
        "hcp_id": user.hcp_id,
        "patient_id": user.patient_id,
    }


def _session(user: User) -> dict:
    return {
        "access_token": sessions.issue(user),
        "token_type": "bearer",
        "user": account_out(user),
    }


def _challenge(user: User, issued: Issued) -> dict:
    out = {
        "verification_token": sessions.issue_verification(user),
        "email": user.email,
        "delivery": issued.delivery,
        "expires_in": issued.expires_in,
        "resend_in": issued.resend_in,
    }
    if issued.dev_code is not None:
        out["dev_otp"] = issued.dev_code
    return out


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


def signup(db: Session, *, name: str, email: str, password: str, confirm: str, role: str) -> dict:
    repo = SqlUserRepository(db)
    email = normalize_email(email)
    if not name.strip():
        raise AuthError(422, "invalid_name", "Enter your name.")
    if not EMAIL.match(email):
        raise AuthError(422, "invalid_email", "Enter a valid email address.")
    if role not in SIGNUP_ROLES:
        # Includes the administrator role, which can never be self-registered.
        raise AuthError(422, "invalid_role", "Choose one of the available roles.")
    validate_password(password, confirm)
    existing = repo.get_by_email(email)
    if existing:
        hint = (
            " Sign in to finish verifying it."
            if not existing.verified and existing.source == AccountSource.SIGNUP
            else ""
        )
        raise AuthError(409, "email_exists", f"An account with this email already exists.{hint}")

    user = repo.create_pending(
        name=name, email=email, password_hash=hash_password(password), role=role
    )
    issued = otp_service.issue(db, user, respect_cooldown=False)
    audit.record(
        db,
        "account_created",
        "user",
        user.id,
        actor=user.username,
        actor_role=user.role,
        detail={"delivery": issued.delivery},
    )
    return _challenge(user, issued)


def _pending_user(db: Session, verification_token: str) -> User:
    user = sessions.resolve_verification(db, verification_token)
    if user is None:
        raise AuthError(401, "verification_expired", "Verification has expired. Sign in again.")
    if user.status == AccountStatus.DISABLED:
        raise AuthError(403, "account_disabled", "This account has been disabled.")
    if user.verified:
        raise AuthError(409, "already_verified", "This account is already verified. Sign in.")
    return user


def verify_otp(db: Session, verification_token: str, code: str) -> dict:
    user = _pending_user(db, verification_token)
    otp_service.verify(db, user, code)
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
        detail={"assigned": assigned},
    )
    return _session(user)


def resend_otp(db: Session, verification_token: str) -> dict:
    user = _pending_user(db, verification_token)
    return _challenge(user, otp_service.issue(db, user))


def login(db: Session, email: str, password: str) -> dict:
    repo = SqlUserRepository(db)
    user = repo.get_by_email(email)
    if user is None:
        verify_password(password, _DUMMY_HASH)
        raise invalid_credentials()
    if not verify_password(password, user.password_hash):
        raise invalid_credentials()
    if user.status == AccountStatus.DISABLED:
        raise AuthError(403, "account_disabled", "This account has been disabled.")
    if not user.verified:
        # Right password, unverified account: hand back a way to finish verification,
        # never a session. A code is issued only if none is currently active.
        status = otp_service.status(db, user)
        extra = {"verification_token": sessions.issue_verification(user), "email": user.email}
        if status["expires_in"] == 0:
            issued = otp_service.issue(db, user, respect_cooldown=False)
            extra.update(_challenge(user, issued))
        else:
            # A code is already active. Say how it was delivered, never the code itself.
            extra.update(status, delivery=get_sender().channel)
        raise AuthError(
            403,
            "verification_required",
            "Verify your email to finish creating the account.",
            **extra,
        )
    repo.record_login(user)
    return _session(user)


def logout(db: Session, user: User) -> None:
    sessions.revoke(user)


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
