"""Account persistence behind an interface.

`SqlUserRepository` works on whatever database the SQLAlchemy session points at, so moving
from the local SQLite file to PostgreSQL is a connection-string change. A different store
(a directory service, an identity provider) would implement `UserRepository` instead.
"""

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User
from app.models.enums import AccountSource, AccountStatus
from app.models.tables import utcnow


def normalize_email(email: str) -> str:
    return email.strip().lower()


class UserRepository(Protocol):
    def get(self, user_id: int) -> User | None: ...
    def get_by_email(self, email: str) -> User | None: ...
    def create_pending(self, *, name: str, email: str, password_hash: str, role: str) -> User: ...
    def activate(self, user: User) -> None: ...
    def set_status(self, user: User, status: str) -> None: ...
    def record_login(self, user: User) -> None: ...


class SqlUserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get(self, user_id: int) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        return self.db.scalar(select(User).where(User.email == normalize_email(email)))

    def create_pending(self, *, name: str, email: str, password_hash: str, role: str) -> User:
        email = normalize_email(email)
        user = User(
            username=email,
            email=email,
            display_name=name.strip(),
            password_hash=password_hash,
            role=role,
            verified=False,
            status=AccountStatus.PENDING,
            source=AccountSource.SIGNUP,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def activate(self, user: User) -> None:
        user.verified, user.status = True, AccountStatus.ACTIVE

    def set_status(self, user: User, status: str) -> None:
        user.status = status

    def record_login(self, user: User) -> None:
        user.last_login_at = utcnow()
