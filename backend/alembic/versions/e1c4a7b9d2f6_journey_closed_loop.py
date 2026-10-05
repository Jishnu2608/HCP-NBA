"""patient journey closed loop: follow-ups, instruction context, last refill, data repairs

- `care_request` gains `owner_user_id`, `due_date`, `visible_to_patient` (a follow-up is
  work a care manager owes the patient by a date). New statuses (`awaiting_hcp`,
  `hcp_responded`) and type (`follow_up`) are plain strings.
- `care_note.request_id` links an instruction to the request it answers.
- `patient_therapy.reported_last_fill`: when the patient says they last collected a supply.
- Repair: engine sends were stamped with the local date and the UTC time of day. Their
  `int_ts` / `outcome_ts` are reset from the audit rows, which hold the true UTC instants.
- Content: the two patient education modules per measure described a guide instead of
  containing it. Their text is replaced by the guide itself as version 2, pending MLR
  review (Compliance approves it again before it can be sent).

Revision ID: e1c4a7b9d2f6
Revises: c5d1a8e3f7b2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1c4a7b9d2f6"
down_revision: str | None = "c5d1a8e3f7b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Routing used to close a consultation although nobody had answered it. Those consultations
# are waiting for the HCP they were routed to.
REOPEN_ROUTED_CONSULTATIONS = """
    UPDATE care_request SET status = 'awaiting_hcp'
    WHERE type = 'consultation' AND status = 'closed' AND assigned_hcp_id IS NOT NULL
      AND resolution LIKE 'Routed to %'
"""


# A second refill of the same medication on the same day was accepted and counted twice.
# Real patients' duplicates are removed (the first one stays); synthetic history is untouched.
REMOVE_DUPLICATE_FILLS = """
    DELETE FROM medication_fill
    WHERE source IN ('patient', 'care_manager')
      AND id NOT IN (
        SELECT MIN(id) FROM medication_fill GROUP BY therapy_id, fill_date
      )
"""


# "Today" was the server's local date and is now the UTC date. Dates a person set "today"
# shortly before the switch can lie a day ahead of the UTC date; they are brought back to it
# so a consent or a refill takes effect now, not tomorrow.
LOCAL_DATES_TO_UTC = (
    "UPDATE consent SET effective_from = date('now') WHERE effective_from > date('now')",
    "UPDATE consent SET effective_to = date('now') WHERE effective_to > date('now')",
    """UPDATE medication_fill SET fill_date = date('now')
       WHERE fill_date > date('now') AND source IN ('patient', 'care_manager')""",
)


def upgrade() -> None:
    with op.batch_alter_table("care_request") as batch:
        batch.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("due_date", sa.Date(), nullable=True))
        batch.add_column(
            sa.Column("visible_to_patient", sa.Boolean(), nullable=False, server_default="1")
        )
        batch.create_foreign_key(
            "fk_care_request_owner_user_id_user", "user", ["owner_user_id"], ["id"]
        )
    with op.batch_alter_table("care_note") as batch:
        batch.add_column(sa.Column("request_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_care_note_request_id_care_request", "care_request", ["request_id"], ["id"]
        )
    with op.batch_alter_table("patient_therapy") as batch:
        batch.add_column(sa.Column("reported_last_fill", sa.Date(), nullable=True))

    # Engine sends and responses: the audit log holds the real UTC instants.
    op.execute(
        """
        UPDATE interaction SET int_ts = (
            SELECT MAX(a.ts) FROM audit_log a
            WHERE a.nba_id = interaction.nba_id AND a.action = 'nba_sent')
        WHERE source = 'nba' AND nba_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM audit_log a
            WHERE a.nba_id = interaction.nba_id AND a.action = 'nba_sent')
        """
    )
    op.execute(
        """
        UPDATE interaction SET outcome_ts = (
            SELECT MAX(a.ts) FROM audit_log a
            WHERE a.nba_id = interaction.nba_id AND a.action = 'response_captured')
        WHERE source = 'nba' AND nba_id IS NOT NULL AND outcome_ts IS NOT NULL AND EXISTS (
            SELECT 1 FROM audit_log a
            WHERE a.nba_id = interaction.nba_id AND a.action = 'response_captured')
        """
    )

    op.execute(REOPEN_ROUTED_CONSULTATIONS)
    for statement in LOCAL_DATES_TO_UTC:
        op.execute(statement)
    op.execute(REMOVE_DUPLICATE_FILLS)

    from app.datagen.content import education_text
    from app.models.enums import Measure

    content = sa.table(
        "content",
        sa.column("content_id", sa.String),
        sa.column("version", sa.Integer),
        sa.column("body", sa.Text),
        sa.column("audience", sa.String),
        sa.column("action_type", sa.String),
        sa.column("topic", sa.String),
        sa.column("mlr_status", sa.String),
        sa.column("effective_date", sa.Date),
        sa.column("expiry_date", sa.Date),
    )
    for measure in Measure:
        for subtopic in ("adherence", "guidelines"):
            op.execute(
                content.update()
                .where(
                    content.c.audience == "PATIENT",
                    content.c.action_type == "education",
                    content.c.topic == f"{measure}_{subtopic}",
                    content.c.version == 1,
                )
                .values(
                    body=education_text(measure, subtopic),
                    version=2,
                    mlr_status="pending",
                    effective_date=None,
                    expiry_date=None,
                )
            )


def downgrade() -> None:
    with op.batch_alter_table("patient_therapy") as batch:
        batch.drop_column("reported_last_fill")
    with op.batch_alter_table("care_note") as batch:
        batch.drop_constraint("fk_care_note_request_id_care_request", type_="foreignkey")
        batch.drop_column("request_id")
    with op.batch_alter_table("care_request") as batch:
        batch.drop_constraint("fk_care_request_owner_user_id_user", type_="foreignkey")
        batch.drop_column("visible_to_patient")
        batch.drop_column("due_date")
        batch.drop_column("owner_user_id")
