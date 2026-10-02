from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import decode_token
from app.models import User

# The identity seam: swap this dependency for an OIDC/SSO validator in production and
# nothing downstream changes, because every endpoint only depends on a User row.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    user_id = decode_token(token)
    user = db.get(User, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not permitted for this role")
        return user

    return dependency


def not_found(what: str = "Not found") -> HTTPException:
    """Out-of-scope rows look exactly like missing rows, so ids cannot be probed."""
    return HTTPException(status.HTTP_404_NOT_FOUND, what)
