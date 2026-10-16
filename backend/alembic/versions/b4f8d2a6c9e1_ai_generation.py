"""AI generation records

- `ai_generation`: one row per AI generation attempt (app/ai/orchestrator.py): operation and
  versions, mode, subject, requester, job, status, provider and model, context fingerprint,
  minimised provenance, gate outcome codes, failure category, timings and the checked result.

New table only; no existing row changes.

Revision ID: b4f8d2a6c9e1
Revises: a7d2e9c4b1f6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4f8d2a6c9e1"
down_revision: str | None = "a7d2e9c4b1f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_generation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation", sa.String(length=48), nullable=False),
        sa.Column("operation_version", sa.String(length=16), nullable=False),
        sa.Column("prompt_version", sa.String(length=16), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("subject_type", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=32), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("job.id"), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=64), nullable=True),
        sa.Column("model_digest", sa.String(length=16), nullable=True),
        sa.Column("context_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("provenance", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("gate_reasons", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("failure", sa.String(length=32), nullable=True),
        sa.Column("output", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index(
        "ix_ai_generation_subject",
        "ai_generation",
        ["operation", "subject_type", "subject_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_generation_subject", table_name="ai_generation")
    op.drop_table("ai_generation")
