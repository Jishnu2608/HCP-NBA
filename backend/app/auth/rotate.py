"""Apply the passwords in .env to the accounts already in the database.

    python -m app.auth.rotate

Editing NBA_ADMIN_PASSWORD or NBA_DEMO_PASSWORD in .env does not change existing accounts
by itself, because the database stores hashes. This command re-hashes the administrator
and the seeded demo accounts from the current settings and ends every session they had
open, without touching demo data. Registered (sign-up) accounts keep their own passwords.
"""

import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.sessions import sessions
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.security import hash_password
from app.models import User
from app.models.enums import AccountSource


def apply_passwords(db: Session) -> dict[str, int]:
    """Returns how many accounts of each kind were updated. Caller commits."""
    settings = get_settings()
    counts = {AccountSource.SYSTEM.value: 0, AccountSource.SEED.value: 0}
    hashes = {AccountSource.SYSTEM: hash_password(settings.secret("admin_password"))}
    seeded = db.scalars(select(User).where(User.source == AccountSource.SEED)).all()
    if seeded:
        hashes[AccountSource.SEED] = hash_password(settings.secret("demo_password"))
    system = db.scalars(select(User).where(User.source == AccountSource.SYSTEM)).all()
    for user in [*system, *seeded]:
        user.password_hash = hashes[AccountSource(user.source)]
        user.token_version += 1  # pending verification tokens stop working
        sessions.revoke_all(db, user)  # sessions opened with the old password end
        counts[user.source] += 1
    audit.record(db, "passwords_rotated", "user", "system+seed", detail=counts)
    return counts


def main() -> int:
    with SessionLocal() as db:
        counts = apply_passwords(db)
        db.commit()
    print(f"Updated {counts['system']} administrator and {counts['seed']} demo accounts.")
    print("Earlier passwords and open sessions for those accounts no longer work.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
