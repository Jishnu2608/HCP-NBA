"""consent records, privacy requests, country of residence

Adds the append-only consent_record table (which document or consent statement, which
version, accepted or withdrawn, when, from where), the privacy_request table, and the
country / US state of each account. Existing accounts get no consent records: they are
asked to accept the current documents at their next sign-in.

Revision ID: f2b6d8a4c1e3
Revises: e7a1c5d9f2b4
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2b6d8a4c1e3"
down_revision: str | None = "e7a1c5d9f2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.add_column(sa.Column("country", sa.String(length=2), nullable=True))
        batch.add_column(sa.Column("region", sa.String(length=3), nullable=True))

    op.create_table(
        "consent_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("jurisdiction", sa.String(length=8), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_consent_record_user_id_user"),
        sa.PrimaryKeyConstraint("id", name="pk_consent_record"),
    )
    op.create_index("ix_consent_record_user_kind", "consent_record", ["user_id", "kind", "id"])

    op.create_table(
        "privacy_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.Column("jurisdiction", sa.String(length=8), nullable=True),
        sa.Column("handled_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("respond_by", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_privacy_request_user_id_user"),
        sa.ForeignKeyConstraint(
            ["handled_by_user_id"], ["user.id"], name="fk_privacy_request_handled_by_user_id_user"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_privacy_request"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_privacy_request_user_id", "privacy_request", ["user_id"])
    op.create_index("ix_privacy_request_status", "privacy_request", ["status"])


def downgrade() -> None:
    op.drop_index("ix_privacy_request_status", table_name="privacy_request")
    op.drop_index("ix_privacy_request_user_id", table_name="privacy_request")
    op.drop_table("privacy_request")
    op.drop_index("ix_consent_record_user_kind", table_name="consent_record")
    op.drop_table("consent_record")
    with op.batch_alter_table("user", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_column("region")
        batch.drop_column("country")
