"""background jobs, per-cycle summary snapshots, audit and interaction lookup indexes

- `job`: engine work run by the worker (app/jobs.py), with a unique `lock_key` so at most
  one engine operation is queued or running at a time.
- `summary_snapshot`: aggregates published after a successful cycle (analytics/snapshots.py).
- Indexes: `audit_log.action`, `audit_log (entity_type, entity_id)`, `interaction.content_id`.

New tables and indexes only; no existing row changes.

Revision ID: a7d2e9c4b1f6
Revises: f3a9c1e7b5d2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7d2e9c4b1f6"
down_revision: str | None = "f3a9c1e7b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("params", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("lock_key", sa.String(length=32), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("worker_id", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.String(length=120), nullable=True),
        sa.Column("result", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.UniqueConstraint("lock_key", name="uq_job_lock_key"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_job_status_requested", "job", ["status", "requested_at"])
    op.create_table(
        "summary_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("cycle_id", sa.Integer(), sa.ForeignKey("engine_cycle.id"), nullable=False),
        sa.Column("calc_version", sa.Integer(), nullable=False),
        sa.Column("source_mark", sa.String(length=64), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("kind", "scope_key", "cycle_id", name="uq_summary_snapshot_scope"),
    )
    op.create_index("ix_summary_snapshot_cycle_id", "summary_snapshot", ["cycle_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_interaction_content_id", "interaction", ["content_id"])


def downgrade() -> None:
    op.drop_index("ix_interaction_content_id", table_name="interaction")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_summary_snapshot_cycle_id", table_name="summary_snapshot")
    op.drop_table("summary_snapshot")
    op.drop_index("ix_job_status_requested", table_name="job")
    op.drop_table("job")
