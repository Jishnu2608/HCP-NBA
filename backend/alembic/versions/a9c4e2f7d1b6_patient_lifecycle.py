"""patient lifecycle: own records, conditions, care requests, notes, deletion

A patient account now owns a patient record created for it instead of claiming a synthetic
one. Adds the record's origin and country, never-reused real patient numbers, conditions,
care requests and care notes, the source of medications and refills, patient invitations
bound to a record, and anonymised privacy requests that outlive a deleted account.

Existing rows: every current patient record is synthetic, every medication and refill is
claims history and confirmed. Accounts that registered before this change and were linked
to a synthetic record are given their own empty record at the next start of the app
(`clinical.records.separate_real_patients`), which also restores the synthetic name.

Revision ID: a9c4e2f7d1b6
Revises: f2b6d8a4c1e3
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a9c4e2f7d1b6"
down_revision: str | None = "f2b6d8a4c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("patient") as batch:
        batch.alter_column("sex", existing_type=sa.String(length=1), nullable=True)
        batch.alter_column("city", existing_type=sa.String(length=64), nullable=True)
        batch.alter_column("state", existing_type=sa.String(length=2), nullable=True)
        batch.alter_column("zip", existing_type=sa.String(length=10), nullable=True)
        batch.alter_column("plan_type", existing_type=sa.String(length=32), nullable=True)
        batch.add_column(
            sa.Column("origin", sa.String(length=16), nullable=False, server_default="synthetic")
        )
        batch.add_column(sa.Column("country", sa.String(length=2), nullable=True))
        batch.add_column(sa.Column("region", sa.String(length=3), nullable=True))
        batch.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        batch.create_index("ix_patient_origin", ["origin"])

    op.create_table(
        "patient_number",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_patient_number"),
        sqlite_autoincrement=True,
    )

    with op.batch_alter_table("patient_therapy") as batch:
        batch.alter_column("measure", existing_type=sa.String(length=32), nullable=True)
        batch.alter_column("days_supply", existing_type=sa.Integer(), nullable=True)
        batch.add_column(
            sa.Column("origin", sa.String(length=16), nullable=False, server_default="claims")
        )
        batch.add_column(
            sa.Column(
                "review_status", sa.String(length=16), nullable=False, server_default="confirmed"
            )
        )
        batch.add_column(sa.Column("dose_instructions", sa.String(length=300), nullable=True))
        batch.add_column(sa.Column("schedule", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("end_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("confirmed_at", sa.DateTime(), nullable=True))
        batch.create_foreign_key(
            "fk_patient_therapy_confirmed_by_user_id_user", "user", ["confirmed_by_user_id"], ["id"]
        )
        batch.create_index("ix_patient_therapy_review_status", ["review_status"])

    with op.batch_alter_table("medication_fill") as batch:
        batch.add_column(
            sa.Column("source", sa.String(length=16), nullable=False, server_default="claims")
        )

    op.create_table(
        "patient_condition",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.String(length=16), nullable=False),
        sa.Column("condition", sa.String(length=32), nullable=False),
        sa.Column("other_text", sa.String(length=120), nullable=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reported_at", sa.DateTime(), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patient.patient_id"], name="fk_patient_condition_patient_id_patient"
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"],
            ["user.id"],
            name="fk_patient_condition_confirmed_by_user_id_user",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_patient_condition"),
    )
    op.create_index("ix_patient_condition_patient_id", "patient_condition", ["patient_id"])

    op.create_table(
        "care_request",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.String(length=16), nullable=False),
        sa.Column("type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("condition_id", sa.Integer(), nullable=True),
        sa.Column("therapy_id", sa.Integer(), nullable=True),
        sa.Column("assigned_hcp_id", sa.String(length=16), nullable=True),
        sa.Column("handled_by_user_id", sa.Integer(), nullable=True),
        sa.Column("resolution", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patient.patient_id"], name="fk_care_request_patient_id_patient"
        ),
        sa.ForeignKeyConstraint(
            ["condition_id"],
            ["patient_condition.id"],
            name="fk_care_request_condition_id_patient_condition",
        ),
        sa.ForeignKeyConstraint(
            ["therapy_id"],
            ["patient_therapy.id"],
            name="fk_care_request_therapy_id_patient_therapy",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_hcp_id"], ["hcp.hcp_id"], name="fk_care_request_assigned_hcp_id_hcp"
        ),
        sa.ForeignKeyConstraint(
            ["handled_by_user_id"], ["user.id"], name="fk_care_request_handled_by_user_id_user"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_care_request"),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_care_request_patient_id", "care_request", ["patient_id"])
    op.create_index("ix_care_request_status", "care_request", ["status"])

    op.create_table(
        "care_note",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.String(length=16), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=True),
        sa.Column("hcp_id", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("text", sa.String(length=1000), nullable=False),
        sa.Column("visible_to_patient", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patient.patient_id"], name="fk_care_note_patient_id_patient"
        ),
        sa.ForeignKeyConstraint(
            ["author_user_id"], ["user.id"], name="fk_care_note_author_user_id_user"
        ),
        sa.ForeignKeyConstraint(["hcp_id"], ["hcp.hcp_id"], name="fk_care_note_hcp_id_hcp"),
        sa.PrimaryKeyConstraint("id", name="pk_care_note"),
    )
    op.create_index("ix_care_note_patient_id", "care_note", ["patient_id"])

    with op.batch_alter_table("invitation", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.add_column(sa.Column("patient_id", sa.String(length=16), nullable=True))
        batch.create_foreign_key(
            "fk_invitation_patient_id_patient", "patient", ["patient_id"], ["patient_id"]
        )

    with op.batch_alter_table(
        "privacy_request", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("subject_label", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(
        "privacy_request", table_kwargs={"sqlite_autoincrement": True}
    ) as batch:
        batch.drop_column("subject_label")
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("invitation", table_kwargs={"sqlite_autoincrement": True}) as batch:
        batch.drop_constraint("fk_invitation_patient_id_patient", type_="foreignkey")
        batch.drop_column("patient_id")
    op.drop_index("ix_care_note_patient_id", table_name="care_note")
    op.drop_table("care_note")
    op.drop_index("ix_care_request_status", table_name="care_request")
    op.drop_index("ix_care_request_patient_id", table_name="care_request")
    op.drop_table("care_request")
    op.drop_index("ix_patient_condition_patient_id", table_name="patient_condition")
    op.drop_table("patient_condition")
    with op.batch_alter_table("medication_fill") as batch:
        batch.drop_column("source")
    with op.batch_alter_table("patient_therapy") as batch:
        batch.drop_index("ix_patient_therapy_review_status")
        batch.drop_constraint("fk_patient_therapy_confirmed_by_user_id_user", type_="foreignkey")
        for column in (
            "confirmed_at", "confirmed_by_user_id", "end_date", "schedule",
            "dose_instructions", "review_status", "origin",
        ):  # fmt: skip
            batch.drop_column(column)
        batch.alter_column("days_supply", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("measure", existing_type=sa.String(length=32), nullable=False)
    op.drop_table("patient_number")
    with op.batch_alter_table("patient") as batch:
        batch.drop_index("ix_patient_origin")
        for column in ("created_by_user_id", "region", "country", "origin"):
            batch.drop_column(column)
        for column, length in (("plan_type", 32), ("zip", 10), ("state", 2), ("city", 64)):
            batch.alter_column(column, existing_type=sa.String(length=length), nullable=False)
        batch.alter_column("sex", existing_type=sa.String(length=1), nullable=False)
