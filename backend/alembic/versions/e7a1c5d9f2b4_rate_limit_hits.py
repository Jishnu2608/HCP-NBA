"""rate limit hits in the database

Rate limits move from process memory to a table, so they survive a restart and are shared
by every server process. Keys are stored only as keyed hashes.

Revision ID: e7a1c5d9f2b4
Revises: d4e8f2a6c3b9
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7a1c5d9f2b4"
down_revision: str | None = "d4e8f2a6c3b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hit",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bucket", sa.String(length=32), nullable=False),
        sa.Column("key_hash", sa.String(length=32), nullable=False),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_rate_limit_hit"),
    )
    op.create_index("ix_rate_limit_hit_lookup", "rate_limit_hit", ["bucket", "key_hash", "ts"])
    op.create_index("ix_rate_limit_hit_ts", "rate_limit_hit", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_rate_limit_hit_ts", table_name="rate_limit_hit")
    op.drop_index("ix_rate_limit_hit_lookup", table_name="rate_limit_hit")
    op.drop_table("rate_limit_hit")
