"""auth accounts and otp

Adds email-based accounts (verification, status, session version, timestamps) to the
user table and the one-time-code table. Existing rows are back-filled so an upgrade in
place keeps working: they become verified, active seed accounts.

Revision ID: a1f4c2e97b30
Revises: 50d7c5d998ec
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1f4c2e97b30"
down_revision: str | None = "50d7c5d998ec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("email", sa.String(length=254), nullable=True))
        batch.add_column(
            sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active")
        )
        batch.add_column(
            sa.Column("source", sa.String(length=16), nullable=False, server_default="seed")
        )
        batch.add_column(
            sa.Column("token_version", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=False, server_default=NOW))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=NOW))
        batch.add_column(sa.Column("last_login_at", sa.DateTime(), nullable=True))

    op.execute("UPDATE \"user\" SET email = lower(username) || '@nba.demo' WHERE email IS NULL")
    op.execute("UPDATE \"user\" SET status = 'disabled' WHERE is_active = 0")

    with op.batch_alter_table("user") as batch:
        batch.alter_column("email", existing_type=sa.String(length=254), nullable=False)
        batch.alter_column(
            "username",
            existing_type=sa.String(length=64),
            type_=sa.String(length=254),
            existing_nullable=False,
        )
        batch.create_unique_constraint("uq_user_email", ["email"])
        batch.create_index("ix_user_status", ["status"])
        batch.drop_column("is_active")

    op.create_table(
        "otp_challenge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("resend_available_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_otp_challenge_user_id_user"),
        sa.PrimaryKeyConstraint("id", name="pk_otp_challenge"),
    )
    op.create_index("ix_otp_challenge_user_id", "otp_challenge", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_otp_challenge_user_id", table_name="otp_challenge")
    op.drop_table("otp_challenge")
    with op.batch_alter_table("user") as batch:
        batch.add_column(
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.drop_index("ix_user_status")
        batch.drop_constraint("uq_user_email", type_="unique")
        for column in (
            "last_login_at",
            "updated_at",
            "created_at",
            "token_version",
            "source",
            "status",
            "verified",
            "email",
        ):
            batch.drop_column(column)
