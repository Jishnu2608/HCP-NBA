from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.auth.sessions import sessions
from app.core.db import get_db
from app.core.permissions import Permission, can_any
from app.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    """The identity seam. Everything downstream depends only on the returned account, so an
    enterprise identity provider replaces `sessions.resolve` and nothing else changes."""
    user = sessions.resolve(db, token)
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            {"code": "not_authenticated", "message": "Sign in to continue."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_permission(*permissions: Permission):
    """Endpoint guard: the account must hold at least one of the given permissions.

    The role is looked up on the account loaded from the database and mapped through
    core/permissions.py. Nothing sent by the client takes part in the decision.
    """

    def dependency(user: User = Depends(get_current_user)) -> User:
        if not can_any(user, *permissions):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                {"code": "forbidden", "message": "Your account is not permitted to do this."},
            )
        return user

    return dependency


def not_found(what: str = "Not found") -> HTTPException:
    """Out-of-scope rows look exactly like missing rows, so ids cannot be probed."""
    return HTTPException(status.HTTP_404_NOT_FOUND, what)
