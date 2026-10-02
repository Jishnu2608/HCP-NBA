from collections.abc import Callable

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth import service
from app.auth.errors import AuthError
from app.core.db import get_db
from app.core.permissions import SIGNUP_ROLES
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

ROLE_INFO = {
    "care_manager": ("Care Manager", "Works with assigned patient adherence cases."),
    "medical_rep": (
        "Medical Representative",
        "Works with assigned HCP engagement and next-best-action recommendations.",
    ),
    "compliance": (
        "Compliance / MLR Reviewer",
        "Reviews content approval, compliance decisions and audit history.",
    ),
    "patient": ("Patient", "Views their own medications, adherence, consent and engagement."),
    "hcp": (
        "Healthcare Professional",
        "Views their own HCP information and relevant engagement and content.",
    ),
}


class SignupBody(BaseModel):
    name: str = Field(max_length=128)
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)
    confirm_password: str = Field(max_length=128)
    role: str


class VerifyBody(BaseModel):
    verification_token: str
    code: str = Field(min_length=1, max_length=12)


class ResendBody(BaseModel):
    verification_token: str


def _run(db: Session, action: Callable[[], dict]) -> dict:
    """Commits on success and also on a handled failure: a wrong code must still count as
    an attempt, and a code issued during a refused sign-in must still exist."""
    try:
        result = action()
    except AuthError:
        db.commit()
        raise
    db.commit()
    return result


@router.get("/roles")
def signup_roles() -> list[dict]:
    """Roles a person may register as. The administrator is not among them."""
    return [
        {"role": role, "label": ROLE_INFO[role][0], "description": ROLE_INFO[role][1]}
        for role in SIGNUP_ROLES
    ]


@router.post("/signup", status_code=201)
def signup(body: SignupBody, db: Session = Depends(get_db)) -> dict:
    """Creates an unverified account and issues a one-time code. No session yet."""
    return _run(
        db,
        lambda: service.signup(
            db,
            name=body.name,
            email=body.email,
            password=body.password,
            confirm=body.confirm_password,
            role=body.role,
        ),
    )


@router.post("/verify-otp")
def verify_otp(body: VerifyBody, db: Session = Depends(get_db)) -> dict:
    """Confirms the code, activates the account, assigns its data and starts a session."""
    return _run(db, lambda: service.verify_otp(db, body.verification_token, body.code))


@router.post("/resend-otp")
def resend_otp(body: ResendBody, db: Session = Depends(get_db)) -> dict:
    return _run(db, lambda: service.resend_otp(db, body.verification_token))


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> dict:
    """Email and password. The form's `username` field carries the email address."""
    return _run(db, lambda: service.login(db, form.username, form.password))


@router.post("/logout")
def logout(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Ends the session on the server: the token stops working immediately."""
    service.logout(db, user)
    db.commit()
    return {"signed_out": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return service.account_out(user)
