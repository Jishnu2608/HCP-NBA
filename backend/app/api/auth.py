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
from app.auth import service
from app.auth.errors import AuthError
from app.auth.repository import normalize_email
from app.auth.service import AuthResult
from app.auth.sessions import SESSION_COOKIE, VERIFY_COOKIE, sessions
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


@router.post("/signup", status_code=201)
def signup(body: SignupBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Registers a patient and issues a one-time code. No session yet. Professional roles
    are not available here: they come only from an invitation."""
    ratelimit.limit(request, "signup", normalize_email(body.email))
    return run(
        db,
        lambda: service.signup(
            db,
            name=body.name,
            email=body.email,
            date_of_birth=body.date_of_birth,
            password=body.password,
            confirm=body.confirm_password,
        ),
        status=201,
    )


@router.post("/verify-otp")
def verify_otp(body: VerifyBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Confirms the code, activates the account, assigns its data and starts a session."""
    pending = request.cookies.get(VERIFY_COOKIE)
    ratelimit.limit(request, "verify", token_hash(pending) if pending else None)
    # Whatever session this browser had before, it ends here (no session fixation).
    sessions.revoke(db, request.cookies.get(SESSION_COOKIE))
    return run(db, lambda: service.verify_otp(db, pending, body.code, _agent(request)))


@router.post("/resend-otp")
def resend_otp(request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    pending = request.cookies.get(VERIFY_COOKIE)
    ratelimit.limit(request, "resend", token_hash(pending) if pending else None)
    return run(db, lambda: service.resend_otp(db, pending))


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)) -> JSONResponse:
    """Email and password. The stored role decides everything after this."""
    ratelimit.limit(request, "login", normalize_email(body.email))
    sessions.revoke(db, request.cookies.get(SESSION_COOKIE))
    return run(db, lambda: service.login(db, body.email, body.password, _agent(request)))


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
def me(user: User = Depends(get_current_user)) -> dict:
    return service.account_out(user)
