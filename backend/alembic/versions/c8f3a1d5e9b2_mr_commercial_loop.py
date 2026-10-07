"""mr commercial loop: HCP tasks, HCP intent, richer outcomes, rep-proposed contacts, product

- `hcp_task`: an HCP's request, a follow-up or a meeting owed by the assigned representative.
- `interaction.intent`, `interaction.outcome_reason`, `interaction.note`.
- `nba.origin` ("engine" or "rep").
- `content.product`.

Revision ID: c8f3a1d5e9b2
Revises: b7e2d4f6a8c1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8f3a1d5e9b2"
down_revision: str | None = "b7e2d4f6a8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hcp_task",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hcp_id", sa.String(length=16), sa.ForeignKey("hcp.hcp_id"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("intent", sa.String(length=24), nullable=True),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column(
            "content_id", sa.String(length=16), sa.ForeignKey("content.content_id"), nullable=True
        ),
        sa.Column("interaction_id", sa.Integer(), sa.ForeignKey("interaction.id"), nullable=True),
        sa.Column("nba_id", sa.Integer(), sa.ForeignKey("nba.id"), nullable=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_by_role", sa.String(length=32), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=True),
        sa.Column("requested_by_hcp", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("outcome_reason", sa.String(length=24), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_hcp_task_hcp_id", "hcp_task", ["hcp_id"])
    op.create_index("ix_hcp_task_status", "hcp_task", ["status"])
    op.create_index("ix_hcp_task_owner_user_id", "hcp_task", ["owner_user_id"])
    with op.batch_alter_table("interaction") as batch:
        batch.add_column(sa.Column("intent", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("outcome_reason", sa.String(length=24), nullable=True))
        batch.add_column(sa.Column("note", sa.String(length=500), nullable=True))
    with op.batch_alter_table("nba") as batch:
        batch.add_column(
            sa.Column("origin", sa.String(length=12), nullable=False, server_default="engine")
        )
    with op.batch_alter_table("content") as batch:
        batch.add_column(sa.Column("product", sa.String(length=120), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("content") as batch:
        batch.drop_column("product")
    with op.batch_alter_table("nba") as batch:
        batch.drop_column("origin")
    with op.batch_alter_table("interaction") as batch:
        for col in ("note", "outcome_reason", "intent"):
            batch.drop_column(col)
    op.drop_index("ix_hcp_task_owner_user_id", "hcp_task")
    op.drop_index("ix_hcp_task_status", "hcp_task")
    op.drop_index("ix_hcp_task_hcp_id", "hcp_task")
    op.drop_table("hcp_task")
