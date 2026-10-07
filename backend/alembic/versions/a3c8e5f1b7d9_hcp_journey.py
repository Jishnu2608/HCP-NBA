"""hcp journey: country of practice, durable audit actor, seen specialty decisions

- `hcp.country`: "US" for the synthetic population; an invited HCP's country of residence
  (from their account). Routing compares it with the patient's country.
- `audit_log.actor_user_id`: the account that acted. Back-filled only where the handle
  belongs to an account that already existed at the time of the row, so rows written by a
  deleted account are never attributed to a later account with the same email.
- `specialty_change_request.seen_at`: when the HCP saw the decision.

Revision ID: a3c8e5f1b7d9
Revises: f7d2b9c4e1a8
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3c8e5f1b7d9"
down_revision: str | None = "f7d2b9c4e1a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BACKFILL = (
    "UPDATE hcp SET country = 'US' WHERE origin = 'synthetic' AND country IS NULL",
    """UPDATE hcp SET country = (SELECT u.country FROM user u WHERE u.hcp_id = hcp.hcp_id)
       WHERE origin = 'invited' AND country IS NULL""",
    """UPDATE hcp SET state = (SELECT u.region FROM user u WHERE u.hcp_id = hcp.hcp_id)
       WHERE origin = 'invited' AND state IS NULL AND country = 'US'""",
    """UPDATE audit_log SET actor_user_id = (
         SELECT u.id FROM user u
         WHERE u.username = audit_log.actor AND u.created_at <= audit_log.ts)
       WHERE actor_user_id IS NULL""",
    # The engine's single specialty field for an invited HCP follows their specialties.
    """UPDATE hcp SET specialty = (SELECT MIN(s.specialty) FROM hcp_specialty s
         WHERE s.hcp_id = hcp.hcp_id) WHERE origin = 'invited' AND specialty IS NULL""",
    # Decisions made before the column existed count as seen.
    "UPDATE specialty_change_request SET seen_at = decided_at WHERE decided_at IS NOT NULL",
)


def upgrade() -> None:
    with op.batch_alter_table("hcp") as batch:
        batch.add_column(sa.Column("country", sa.String(length=8), nullable=True))
    with op.batch_alter_table("audit_log") as batch:
        batch.add_column(sa.Column("actor_user_id", sa.Integer(), nullable=True))
        batch.create_index("ix_audit_log_actor_user_id", ["actor_user_id"])
    with op.batch_alter_table("specialty_change_request") as batch:
        batch.add_column(sa.Column("seen_at", sa.DateTime(), nullable=True))
    if op.get_bind().dialect.name != "sqlite":
        return  # the back-fills below are written for the POC's SQLite database
    for statement in BACKFILL:
        op.execute(statement)


def downgrade() -> None:
    with op.batch_alter_table("specialty_change_request") as batch:
        batch.drop_column("seen_at")
    with op.batch_alter_table("audit_log") as batch:
        batch.drop_index("ix_audit_log_actor_user_id")
        batch.drop_column("actor_user_id")
    with op.batch_alter_table("hcp") as batch:
        batch.drop_column("country")
