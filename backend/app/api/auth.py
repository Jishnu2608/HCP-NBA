from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import create_token, verify_password
from app.models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

ROLE_ORDER = ["care_manager", "patient", "medical_rep", "hcp", "compliance", "admin"]
# One persona per role is enough for the switcher; staff pools are trimmed to the first.
FEATURED = {"admin", "compliance1", "rep01", "cm01"}


class DemoLogin(BaseModel):
    username: str


def user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "hcp_id": user.hcp_id,
        "patient_id": user.patient_id,
    }


def _token_response(user: User) -> dict:
    return {
        "access_token": create_token(user.id, user.role),
        "token_type": "bearer",
        "user": user_out(user),
    }


@router.post("/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.username == form.username))
    if user is None or not user.is_active or not verify_password(form.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect username or password")
    return _token_response(user)


@router.get("/personas")
def personas(db: Session = Depends(get_db)) -> list[dict]:
    """Seeded demo personas for the one-click switcher. Empty outside demo mode."""
    if not get_settings().demo_mode:
        return []
    users = db.scalars(select(User).where(User.is_active)).all()
    shown = [u for u in users if u.username in FEATURED or u.hcp_id or u.patient_id]
    shown.sort(key=lambda u: (ROLE_ORDER.index(u.role), u.username))
    return [user_out(u) for u in shown]


@router.post("/demo-login")
def demo_login(body: DemoLogin, db: Session = Depends(get_db)) -> dict:
    """Passwordless persona switch. Exists only while NBA_DEMO_MODE is on."""
    if not get_settings().demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    user = db.scalar(select(User).where(User.username == body.username, User.is_active))
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown persona")
    return _token_response(user)


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return user_out(user)
