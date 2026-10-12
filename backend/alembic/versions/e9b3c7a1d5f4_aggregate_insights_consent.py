"""aggregate insights consent for synthetic patients (data only, no schema change)

The new consent purpose `aggregate_insights` (use of a patient's information in de-identified
group statistics for HCP engagement) is opt-in. Newly generated synthetic patients receive a
recorded choice from the generator (`datagen.reference.aggregate_insights_opt_in`); this
migration gives existing synthetic patients the same deterministic choice, effective from
their enrolment (their earliest consent record), source `enrollment`.

Real patients (self-registered or clinic) get nothing: for them the purpose stays off until
they switch it on themselves on their consent page.

Revision ID: e9b3c7a1d5f4
Revises: e5b8c1d7f3a2
"""

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e9b3c7a1d5f4"
down_revision: str | None = "e5b8c1d7f3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PURPOSE = "aggregate_insights"
# Frozen copy of datagen.reference.aggregate_insights_opt_in at the time of this migration.
RATE = 0.75


def _opt_in(patient_id: str) -> bool:
    digest = hashlib.sha256(f"aggregate_insights:{patient_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < RATE


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT p.patient_id, MIN(c.effective_from) FROM patient p "
            "JOIN consent c ON c.patient_id = p.patient_id "
            "WHERE p.origin = 'synthetic' AND NOT EXISTS ("
            "  SELECT 1 FROM consent a WHERE a.patient_id = p.patient_id AND a.purpose = :purpose"
            ") GROUP BY p.patient_id"
        ),
        {"purpose": PURPOSE},
    ).all()
    for patient_id, enrolled in rows:
        bind.execute(
            sa.text(
                "INSERT INTO consent (patient_id, purpose, channel, granted, effective_from, "
                "effective_to, source) VALUES (:pid, :purpose, NULL, :granted, :start, NULL, "
                "'enrollment')"
            ),
            {
                "pid": patient_id,
                "purpose": PURPOSE,
                "granted": _opt_in(patient_id),
                "start": enrolled,
            },
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM consent WHERE purpose = :purpose AND source = 'enrollment' AND "
            "patient_id IN (SELECT patient_id FROM patient WHERE origin = 'synthetic')"
        ).bindparams(purpose=PURPOSE)
    )
