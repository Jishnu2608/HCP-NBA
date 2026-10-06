"""care manager journey: dismissal reasons, reviewed responses, data repairs

- `patient_condition.dismissed_reason` and `patient_therapy.dismissed_reason`: why a care
  manager did not add a reported entry. It used to live only in the linked request's
  resolution and was lost when no open request existed.
- `nba.response_reviewed_ts`: when the care team looked at a real patient's response to
  what was sent. Until then the response counts as work for the care manager.
- Repairs:
  - dismissal reasons recovered from the closed review requests that carried them;
  - adherence snapshots dated after today (written on the local date before the UTC time
    base) are removed, so they can never be read as current;
  - activity, send and response instants later than now (same-day events stamped at noon
    UTC) are brought back to now;
  - open care requests of real patients name their current care manager as handler.

Revision ID: f7d2b9c4e1a8
Revises: e1c4a7b9d2f6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f7d2b9c4e1a8"
down_revision: str | None = "e1c4a7b9d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RECOVER_REASONS = (
    """UPDATE patient_condition SET dismissed_reason = (
         SELECT substr(r.resolution, 12) FROM care_request r
         WHERE r.condition_id = patient_condition.id AND r.resolution LIKE 'Not added: %'
         ORDER BY r.id DESC LIMIT 1)
       WHERE status = 'dismissed' AND dismissed_reason IS NULL""",
    """UPDATE patient_therapy SET dismissed_reason = (
         SELECT substr(r.resolution, 12) FROM care_request r
         WHERE r.therapy_id = patient_therapy.id AND r.resolution LIKE 'Not added: %'
         ORDER BY r.id DESC LIMIT 1)
       WHERE review_status = 'dismissed' AND dismissed_reason IS NULL""",
)

FUTURE_SNAPSHOTS = "DELETE FROM adherence_snapshot WHERE as_of_date > date('now')"

FUTURE_INSTANTS = (
    """UPDATE patient SET last_activity_at = datetime('now')
       WHERE last_activity_at > datetime('now')""",
    "UPDATE interaction SET outcome_ts = datetime('now') WHERE outcome_ts > datetime('now')",
)

CURRENT_HANDLER = """
    UPDATE care_request SET handled_by_user_id = (
        SELECT cmp.care_manager_user_id FROM care_manager_patient cmp
        WHERE cmp.patient_id = care_request.patient_id
        ORDER BY cmp.care_manager_user_id LIMIT 1)
    WHERE status != 'closed'
      AND handled_by_user_id IS NOT NULL
      AND patient_id IN (SELECT patient_id FROM patient WHERE origin != 'synthetic')
      AND handled_by_user_id NOT IN (
        SELECT care_manager_user_id FROM care_manager_patient
        WHERE care_manager_patient.patient_id = care_request.patient_id)
      AND EXISTS (SELECT 1 FROM care_manager_patient c2
                  WHERE c2.patient_id = care_request.patient_id)
"""


def upgrade() -> None:
    with op.batch_alter_table("patient_condition") as batch:
        batch.add_column(sa.Column("dismissed_reason", sa.String(length=300), nullable=True))
    with op.batch_alter_table("patient_therapy") as batch:
        batch.add_column(sa.Column("dismissed_reason", sa.String(length=300), nullable=True))
    with op.batch_alter_table("nba") as batch:
        batch.add_column(sa.Column("response_reviewed_ts", sa.DateTime(), nullable=True))
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return  # the repairs below are written for the POC's SQLite database
    for statement in (*RECOVER_REASONS, FUTURE_SNAPSHOTS, *FUTURE_INSTANTS, CURRENT_HANDLER):
        op.execute(statement)


def downgrade() -> None:
    with op.batch_alter_table("nba") as batch:
        batch.drop_column("response_reviewed_ts")
    with op.batch_alter_table("patient_therapy") as batch:
        batch.drop_column("dismissed_reason")
    with op.batch_alter_table("patient_condition") as batch:
        batch.drop_column("dismissed_reason")
