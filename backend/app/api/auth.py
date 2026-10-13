"""Sign-up (patients), one-time code, sign-in and sign-out.

Session and verification tokens travel only in HttpOnly cookies set here; response bodies
never contain them. Every state-changing request also passes the CSRF and origin checks in
core/http_security.py.
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.schemas import StrictBody
from app.auth import recovery, service
from app.auth.errors import AuthError
from app.auth.repository import normalize_email
from app.auth.service import AuthResult
from app.auth.sessions import SESSION_COOKIE, VERIFY_COOKIE, Decoy, sessions
from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import token_hash
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupBody(StrictBody):
    name: str = Field(max_length=128)
    email: str = Field(max_length=254)
    date_of_birth: str = Field(max_length=10)
    password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)
    country: str = Field(max_length=2)
    region: str | None = Field(default=None, max_length=3)
    # Both must be actively ticked; neither has a default.
    accept_terms: bool
    consent_health_data: bool


class LoginBody(StrictBody):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


class VerifyBody(StrictBody):
    code: str = Field(min_length=1, max_length=12)


# --- Cookies ---------------------------------------------------------------------------------


def set_session_cookie(response: JSONResponse, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.access_token_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def set_verify_cookie(response: JSONResponse, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        VERIFY_COOKIE,
        token,
        max_age=settings.verification_token_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/auth",
    )


def clear_session_cookie(response: JSONResponse) -> None:
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=get_settings().cookie_secure, httponly=True,
        samesite="lax",
    )  # fmt: skip


def clear_verify_cookie(response: JSONResponse) -> None:
    response.delete_cookie(
        VERIFY_COOKIE, path="/api/auth", secure=get_settings().cookie_secure, httponly=True,
        samesite="strict",
    )  # fmt: skip


def respond(result: AuthResult, status: int = 200) -> JSONResponse:
    response = JSONResponse(result.body, status_code=status)
    if result.session_token:
        set_session_cookie(response, result.session_token)
    if result.verify_token:
        set_verify_cookie(response, result.verify_token)
    elif result.clear_verify:
        clear_verify_cookie(response)
    return response


def run(db: Session, action: Callable[[], AuthResult], status: int = 200) -> JSONResponse:
    """Commits on success and also on a handled failure: a wrong code must still count as
    an attempt, and a code issued during a refused sign-in must still exist. A failure that
    carries a verification token (sign-in of an unverified account, a decoy attempt) still
    sets the cookie."""
    try:
        result = action()
    except AuthError as exc:
        db.commit()
        if exc.verify_token is None:
            raise
        response = JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
        )
        set_verify_cookie(response, exc.verify_token)
        return response
    db.commit()
    return respond(result, status)


def _agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


# --- Routes ----------------------------------------------------------------------------------


def _pending_identity(db: Session, token: str | None, ip: str) -> tuple[str, str]:
    """(rate-limit key for code entry, email) of the account waiting for its code. A new
    code issues a new verification token, so the key is the account, not the token."""
    found = sessions.resolve_verification(db, token)
    if isinstance(found, Decoy):
        return f"decoy:{found.email}", found.email
    if found is not None:
        return f"user:{found.id}", found.email
    return f"none:{ip}", ""


def counted_send(db: Session, ip: str, email: str, action: Callable[[], AuthResult]):
    """Runs an action that emails a code (or the equivalent notice), within the per email +
    address limit; the attempt is counted only when the action succeeds."""

    def wrapped() -> AuthResult:
        result = action()
        ratelimit.record(db, "otp_send", f"{normalize_email(email)}|{ip}")
        return result

    ratelimit.check(db, "otp_send", f"{normalize_email(email)}|{ip}")
    return wrapped


@router.post("/signup", status_code=201)
def signup(body: SignupBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Registers a patient and issues a one-time code. No session yet. Professional roles
    are not available here: they come only from an invitation. The answer is the same
    whether or not the email already has an account."""
    ip = ratelimit.client_ip(request)
    ratelimit.consume(db, "signup_ip", ip)
    action = counted_send(
        db,
        ip,
        body.email,
        lambda: service.signup(
            db,
            name=body.name,
            email=body.email,
            date_of_birth=body.date_of_birth,
            password=body.password,
            confirm=body.confirm_password,
            country=body.country,
            region=body.region,
            accept_terms=body.accept_terms,
            consent_health_data=body.consent_health_data,
        ),
    )
    return run(db, action, status=201)


@router.post("/verify-otp")
def verify_otp(body: VerifyBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Confirms the code, activates the account, assigns its data and starts a session."""
    pending = request.cookies.get(VERIFY_COOKIE)
    key, _ = _pending_identity(db, pending, ratelimit.client_ip(request))
    ratelimit.consume(db, "otp_verify", key)
    # Whatever session this browser had before, it ends here (no session fixation).
    sessions.revoke(db, request.cookies.get(SESSION_COOKIE))
    return run(db, lambda: service.verify_otp(db, pending, body.code, _agent(request)))


@router.post("/resend-otp")
def resend_otp(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    pending = request.cookies.get(VERIFY_COOKIE)
    ip = ratelimit.client_ip(request)
    _, email = _pending_identity(db, pending, ip)
    return run(db, counted_send(db, ip, email, lambda: service.resend_otp(db, pending)))


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Email and password. The stored role decides everything after this. Only failed
    attempts count towards the limit; see core/ratelimit.py."""
    ip = ratelimit.client_ip(request)
    account_key, ip_key = service.login_keys(body.email, ip)
    ratelimit.check(db, "login_fail_account", account_key)
    ratelimit.check(db, "login_fail_ip", ip_key)
    sessions.revoke(db, request.cookies.get(SESSION_COOKIE))
    return run(db, lambda: service.login(db, body.email, body.password, _agent(request), ip))


@router.post("/logout")
def logout(
    request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> JSONResponse:
    """Ends this browser's session on the server: the cookie stops working immediately."""
    service.logout(db, user, request.cookies.get(SESSION_COOKIE))
    db.commit()
    response = JSONResponse({"signed_out": True})
    clear_session_cookie(response)
    return response


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    return service.account_out(user, db)


# --- Forgot password (auth/recovery.py) ---------------------------------------------------


class ResetRequestBody(StrictBody):
    email: str = Field(max_length=254)


class ResetCompleteBody(StrictBody):
    password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)


def _reset_cookie(response: JSONResponse, name: str, token: str, minutes: int) -> None:
    response.set_cookie(
        name, token, max_age=minutes * 60, httponly=True, secure=get_settings().cookie_secure,
        samesite="strict", path=recovery.COOKIE_PATH,
    )  # fmt: skip


def _clear_reset_cookie(response: JSONResponse, name: str) -> None:
    response.delete_cookie(
        name, path=recovery.COOKIE_PATH, secure=get_settings().cookie_secure, httponly=True,
        samesite="strict",
    )  # fmt: skip


def _reset_run(db: Session, action: Callable[[], recovery.Step], status: int = 200) -> JSONResponse:
    """Commits on success and on a handled refusal (a wrong code still counts as an attempt,
    a refused transaction stays ended). An unexpected failure rolls everything back."""
    try:
        step = action()
    except AuthError:
        db.commit()
        raise
    db.commit()
    response = JSONResponse(step.body, status_code=status)
    if step.transaction_token:
        _reset_cookie(
            response, recovery.TRANSACTION_COOKIE, step.transaction_token,
            recovery.TRANSACTION_MINUTES,
        )  # fmt: skip
        _clear_reset_cookie(response, recovery.AUTHORIZATION_COOKIE)
    if step.authorization_token:
        _reset_cookie(
            response, recovery.AUTHORIZATION_COOKIE, step.authorization_token,
            recovery.AUTHORIZATION_MINUTES,
        )  # fmt: skip
    if step.clear_cookies:
        _clear_reset_cookie(response, recovery.TRANSACTION_COOKIE)
        _clear_reset_cookie(response, recovery.AUTHORIZATION_COOKIE)
        clear_session_cookie(response)
    return response


def _transaction_key(request: Request) -> str:
    token = request.cookies.get(recovery.TRANSACTION_COOKIE)
    return f"reset:{token_hash(token)}" if token else f"reset-none:{ratelimit.client_ip(request)}"


@router.post("/password-reset/request", status_code=202)
def password_reset_request(
    body: ResetRequestBody, request: Request, db: Session = Depends(get_db)
) -> JSONResponse:
    """Starts a password reset. The same answer for every address: eligible, unknown, an
    administrator, disabled or unverified. Only an eligible account is emailed a code."""
    ip = ratelimit.client_ip(request)
    ratelimit.consume(db, "reset_request_ip", ip)
    key = f"{normalize_email(body.email)}|{ip}"
    ratelimit.check(db, "otp_send", key)

    def action() -> recovery.Step:
        step = recovery.request(db, body.email)
        ratelimit.record(db, "otp_send", key)
        return step

    return _reset_run(db, action, status=202)


@router.post("/password-reset/resend", status_code=202)
def password_reset_resend(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    key = _transaction_key(request)
    ratelimit.check(db, "otp_send", key)

    def action() -> recovery.Step:
        step = recovery.resend(db, request.cookies.get(recovery.TRANSACTION_COOKIE))
        ratelimit.record(db, "otp_send", key)
        return step

    return _reset_run(db, action, status=202)


@router.post("/password-reset/verify")
def password_reset_verify(
    body: VerifyBody, request: Request, db: Session = Depends(get_db)
) -> JSONResponse:
    ratelimit.consume(db, "otp_verify", _transaction_key(request))
    token = request.cookies.get(recovery.TRANSACTION_COOKIE)
    return _reset_run(db, lambda: recovery.verify(db, token, body.code))


@router.post("/password-reset/complete")
def password_reset_complete(
    body: ResetCompleteBody, request: Request, db: Session = Depends(get_db)
) -> JSONResponse:
    """Sets the new password. Needs the transaction and the reset authorization issued by a
    successful code check, both from this browser's cookies; never signs in."""
    return _reset_run(
        db,
        lambda: recovery.complete(
            db,
            request.cookies.get(recovery.TRANSACTION_COOKIE),
            request.cookies.get(recovery.AUTHORIZATION_COOKIE),
            body.password,
            body.confirm_password,
        ),
    )
