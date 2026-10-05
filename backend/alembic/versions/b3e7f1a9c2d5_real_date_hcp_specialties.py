"""real date, HCP origin and specialties, specialty change requests

- The adjustable demo date is gone: the application uses the real date. The stored
  `demo_as_of_date` becomes `simulated_through` (how far the outcome simulator has played
  the synthetic population), which the next cycle catches up to today.
- HCPs gain an origin (`synthetic` / `invited`); an invited HCP gets a blank record, so the
  practice fields become optional. Specialties move to `hcp_specialty` (several per HCP),
  back-filled from each synthetic HCP's single specialty. `hcp_number` issues never-reused
  ids for invited HCPs. Invitations carry the inviter's chosen specialties.
- `specialty_change_request`: an HCP asks, an administrator approves or rejects.

Invited HCP accounts that still use a synthetic record are given their own blank record at
the next start of the app (`clinical.hcps.separate_real_hcps`).

Revision ID: b3e7f1a9c2d5
Revises: a9c4e2f7d1b6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3e7f1a9c2d5"
down_revision: str | None = "a9c4e2f7d1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NULLABLE = (
    ("npi", 10), ("specialty", 64), ("taxonomy_code", 16), ("organization", 128),
    ("city", 64), ("state", 2), ("zip", 10),
)  # fmt: skip


def upgrade() -> None:
    op.execute("UPDATE engine_config SET key = 'simulated_through' WHERE key = 'demo_as_of_date'")

    with op.batch_alter_table("hcp") as batch:
        for column, length in NULLABLE:
            batch.alter_column(column, existing_type=sa.String(length=length), nullable=True)
        batch.add_column(
            sa.Column("origin", sa.String(length=16), nullable=False, server_default="synthetic")
        )
        batch.create_index("ix_hcp_origin", ["origin"])

    op.create_table(
        "hcp_specialty",
        sa.Column("hcp_id", sa.String(length=16), nullable=False),
        sa.Column("specialty", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["hcp_id"], ["hcp.hcp_id"], name="fk_hcp_specialty_hcp_id_hcp"),
        sa.PrimaryKeyConstraint("hcp_id", "specialty", name="pk_hcp_specialty"),
    )
    op.execute(
        "INSERT INTO hcp_specialty (hcp_id, specialty) "
        "SELECT hcp_id, specialty FROM hcp WHERE specialty IS NOT NULL"
    )
    op.create_table(
        "hcp_number",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_hcp_number"),
        sqlite_autoincrement=True,
    )
    op.create_table(
        "specialty_change_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hcp_id", sa.String(length=16), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("requested", sa.JSON(), nullable=False),
        sa.Column("previous", sa.JSON(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), nullable=True),
        sa.Column("decision_notes", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["hcp_id"], ["hcp.hcp_id"], name="fk_specialty_change_request_hcp_id_hcp"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["user.id"],
            name="fk_specialty_change_request_requested_by_user_id_user",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"],
            ["user.id"],
            name="fk_specialty_change_request_reviewer_user_id_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_specialty_change_request"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_specialty_change_request_hcp_id", "specialty_change_request", ["hcp_id"])
    op.create_index("ix_specialty_change_request_status", "specialty_change_request", ["status"])

    with op.batch_alter_table("invitation", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.add_column(sa.Column("specialties", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invitation", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_column("specialties")
    op.drop_index("ix_specialty_change_request_status", table_name="specialty_change_request")
    op.drop_index("ix_specialty_change_request_hcp_id", table_name="specialty_change_request")
    op.drop_table("specialty_change_request")
    op.drop_table("hcp_number")
    op.drop_table("hcp_specialty")
    with op.batch_alter_table("hcp") as batch:
        batch.drop_index("ix_hcp_origin")
        batch.drop_column("origin")
        for column, length in NULLABLE:
            batch.alter_column(column, existing_type=sa.String(length=length), nullable=False)
    op.execute("UPDATE engine_config SET key = 'demo_as_of_date' WHERE key = 'simulated_through'")
