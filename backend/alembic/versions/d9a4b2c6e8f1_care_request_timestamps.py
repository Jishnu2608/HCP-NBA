"""care request timestamps: when a consultation was routed, answered and closed

- `care_request.routed_at`, `responded_at`, `closed_at`.
- Back-filled from the audit log: the answer (`consultation_answered`, with the request id),
  the routing that preceded it (the latest `hcp_assigned` for the same patient and HCP), and
  the closing (`care_request_updated` with status closed, else the last update of a closed
  request).

Revision ID: d9a4b2c6e8f1
Revises: c8f3a1d5e9b2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9a4b2c6e8f1"
down_revision: str | None = "c8f3a1d5e9b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("care_request") as batch:
        batch.add_column(sa.Column("routed_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("responded_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("closed_at", sa.DateTime(), nullable=True))

    bind = op.get_bind()
    answers = bind.execute(
        sa.text(
            "SELECT CAST(json_extract(detail, '$.request') AS INTEGER), entity_id, "
            "json_extract(detail, '$.hcp'), MAX(ts) FROM audit_log "
            "WHERE action = 'consultation_answered' GROUP BY 1"
        )
    ).fetchall()
    for request_id, patient_id, hcp_id, answered in answers:
        routed = bind.scalar(
            sa.text(
                "SELECT MAX(ts) FROM audit_log WHERE action = 'hcp_assigned' "
                "AND entity_id = :p AND json_extract(detail, '$.hcp') = :h AND ts <= :t"
            ),
            {"p": patient_id, "h": hcp_id, "t": answered},
        )
        bind.execute(
            sa.text(
                "UPDATE care_request SET responded_at = :a, routed_at = :r "
                "WHERE id = :id AND type = 'consultation' AND assigned_hcp_id = :h"
            ),
            {"a": answered, "r": routed, "id": request_id, "h": hcp_id},
        )
    # Still waiting for an HCP: routed by the latest assignment to that HCP.
    waiting = bind.execute(
        sa.text(
            "SELECT id, patient_id, assigned_hcp_id FROM care_request "
            "WHERE status = 'awaiting_hcp' AND routed_at IS NULL"
        )
    ).fetchall()
    for request_id, patient_id, hcp_id in waiting:
        routed = bind.scalar(
            sa.text(
                "SELECT MAX(ts) FROM audit_log WHERE action = 'hcp_assigned' "
                "AND entity_id = :p AND json_extract(detail, '$.hcp') = :h"
            ),
            {"p": patient_id, "h": hcp_id},
        )
        bind.execute(
            sa.text("UPDATE care_request SET routed_at = :r WHERE id = :id"),
            {"r": routed, "id": request_id},
        )
    bind.execute(
        sa.text(
            "UPDATE care_request SET closed_at = (SELECT MAX(ts) FROM audit_log "
            "WHERE action = 'care_request_updated' "
            "AND json_extract(detail, '$.status') = 'closed' "
            "AND CAST(json_extract(detail, '$.request') AS INTEGER) = care_request.id) "
            "WHERE status = 'closed'"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE care_request SET closed_at = updated_at "
            "WHERE status = 'closed' AND closed_at IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("care_request") as batch:
        batch.drop_column("closed_at")
        batch.drop_column("responded_at")
        batch.drop_column("routed_at")
