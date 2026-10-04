"""never reuse account and invitation ids

SQLite gives a new row the highest id + 1, so deleting the newest account lets the next
one take its id, and its audit history appears to belong to the new account. Rebuilding
the tables with AUTOINCREMENT makes ids strictly increasing for good (also across a demo
reset). PostgreSQL sequences never reuse ids, so there this is a no-op.

Revision ID: d4e8f2a6c3b9
Revises: c3d9e5a7b1f2
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d4e8f2a6c3b9"
down_revision: str | None = "c3d9e5a7b1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("user", "invitation")


def _rebuild(autoincrement: bool) -> None:
    if op.get_bind().dialect.name != "sqlite":
        return
    for table in TABLES:
        # The copy keeps every row with its id; SQLite then continues numbering after the
        # highest id ever used.
        with op.batch_alter_table(
            table, recreate="always", table_kwargs={"sqlite_autoincrement": autoincrement}
        ):
            pass


def upgrade() -> None:
    _rebuild(True)


def downgrade() -> None:
    _rebuild(False)
