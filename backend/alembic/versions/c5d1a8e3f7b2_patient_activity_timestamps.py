"""patient created_at, updated_at, last_activity_at

Patient lists are ordered by latest activity, newest first. Existing rows are filled from
each patient's own history at the next start of the app (`clinical.activity.backfill`).

Revision ID: c5d1a8e3f7b2
Revises: b3e7f1a9c2d5
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c5d1a8e3f7b2"
down_revision: str | None = "b3e7f1a9c2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("patient") as batch:
        batch.add_column(sa.Column("created_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_activity_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_patient_last_activity_at", ["last_activity_at"])


def downgrade() -> None:
    with op.batch_alter_table("patient") as batch:
        batch.drop_index("ix_patient_last_activity_at")
        batch.drop_column("last_activity_at")
        batch.drop_column("updated_at")
        batch.drop_column("created_at")
