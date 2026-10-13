"""password reset transactions (forgot password for every role except the administrator)

New table `password_reset` only; no existing table or row changes.

Revision ID: f3a9c1e7b5d2
Revises: e9b3c7a1d5f4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f3a9c1e7b5d2"
down_revision: str | None = "e9b3c7a1d5f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "password_reset",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("transaction_hash", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("resend_available_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=True),
        sa.Column("authorization_hash", sa.String(length=64), nullable=True),
        sa.Column("authorization_expires_at", sa.DateTime(), nullable=True),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("transaction_hash", name="uq_password_reset_transaction_hash"),
        sa.UniqueConstraint("authorization_hash", name="uq_password_reset_authorization_hash"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_password_reset_user_id", "password_reset", ["user_id"])
    op.create_index("ix_password_reset_expires_at", "password_reset", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_password_reset_expires_at", table_name="password_reset")
    op.drop_index("ix_password_reset_user_id", table_name="password_reset")
    op.drop_table("password_reset")
