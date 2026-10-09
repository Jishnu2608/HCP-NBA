"""patient care plan: tracked measurements and targets, readings, check-ins, coins, board

- `care_plan`: daily check-in quota and the medicines confirmation, set by the care manager.
- `care_plan_measure`: tracked measurements with optional target ranges.
- `health_reading`: what the patient recorded.
- `checkin`: one completed plan item per day (unique per patient, day, item).
- `coin_ledger`: one coin per qualifying day (unique per patient and day).
- `leaderboard_profile`: opt-in alias for the monthly board (private by default).

Revision ID: e5b8c1d7f3a2
Revises: d9a4b2c6e8f1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5b8c1d7f3a2"
down_revision: str | None = "d9a4b2c6e8f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "care_plan",
        sa.Column(
            "patient_id",
            sa.String(length=16),
            sa.ForeignKey("patient.patient_id"),
            primary_key=True,
        ),
        sa.Column("checkin_quota", sa.Integer(), nullable=False),
        sa.Column("medication_check", sa.Boolean(), nullable=False),
        sa.Column("updated_by_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "care_plan_measure",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "patient_id", sa.String(length=16), sa.ForeignKey("patient.patient_id"), nullable=False
        ),
        sa.Column("measure", sa.String(length=32), nullable=False),
        sa.Column("targets", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("patient_id", "measure", name="uq_care_plan_measure"),
    )
    op.create_index("ix_care_plan_measure_patient_id", "care_plan_measure", ["patient_id"])
    op.create_table(
        "health_reading",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "patient_id", sa.String(length=16), sa.ForeignKey("patient.patient_id"), nullable=False
        ),
        sa.Column("measure", sa.String(length=32), nullable=False),
        sa.Column("values", sa.JSON(), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_health_reading_patient_taken", "health_reading", ["patient_id", "taken_at"])
    op.create_table(
        "checkin",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "patient_id", sa.String(length=16), sa.ForeignKey("patient.patient_id"), nullable=False
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("item", sa.String(length=32), nullable=False),
        sa.Column("reading_id", sa.Integer(), sa.ForeignKey("health_reading.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("patient_id", "day", "item", name="uq_checkin_item_day"),
    )
    op.create_index("ix_checkin_patient_id", "checkin", ["patient_id"])
    op.create_table(
        "coin_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "patient_id", sa.String(length=16), sa.ForeignKey("patient.patient_id"), nullable=False
        ),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("patient_id", "day", name="uq_coin_patient_day"),
    )
    op.create_index("ix_coin_ledger_patient_id", "coin_ledger", ["patient_id"])
    op.create_table(
        "leaderboard_profile",
        sa.Column(
            "patient_id",
            sa.String(length=16),
            sa.ForeignKey("patient.patient_id"),
            primary_key=True,
        ),
        sa.Column("alias", sa.String(length=48), nullable=False, unique=True),
        sa.Column("opted_in", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("leaderboard_profile")
    op.drop_index("ix_coin_ledger_patient_id", "coin_ledger")
    op.drop_table("coin_ledger")
    op.drop_index("ix_checkin_patient_id", "checkin")
    op.drop_table("checkin")
    op.drop_index("ix_health_reading_patient_taken", "health_reading")
    op.drop_table("health_reading")
    op.drop_index("ix_care_plan_measure_patient_id", "care_plan_measure")
    op.drop_table("care_plan_measure")
    op.drop_table("care_plan")
