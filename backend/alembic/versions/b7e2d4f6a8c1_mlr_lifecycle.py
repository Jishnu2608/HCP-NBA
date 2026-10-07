"""mlr lifecycle: content versions and lineage, structured review, threads, delivered wording

- `content`: lineage, previous version, origin, author, submission time, claims, indication,
  safety, labelling, jurisdictions, decision time, review owner, approver, withdrawal reason.
- `content_review`: version, previous and resulting status, medical / legal / regulatory
  verdicts, representative-facing feedback, when the author saw the decision.
- `content_message` and `content_message_read`: the conversation attached to content.
- `interaction.draft_id`: the exact wording delivered.

Back-fill (SQLite): library rows get their lineage and synthetic reviewable facts; the updated
cohort summary becomes version 2 of the first one; when a lineage holds more than one approved
version, the older ones become superseded and their open recommendations expire (audited).

Revision ID: b7e2d4f6a8c1
Revises: a3c8e5f1b7d9
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e2d4f6a8c1"
down_revision: str | None = "a3c8e5f1b7d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SAFETY = (
    "Synthetic demonstration material: makes no product efficacy or safety claim. Patients "
    "follow their prescriber's advice and report side effects to their care team."
)
LABELLING = "No product labelling applies (non-promotional adherence material)."

BACKFILL = (
    "UPDATE content SET origin = 'library' WHERE origin IS NULL",
    "UPDATE content SET lineage_id = content_id WHERE lineage_id IS NULL",
    """UPDATE content SET claims = json_array(json_object(
         'text', title || ': illustrative synthetic claim',
         'reference', 'Synthetic library source ' || content_id || ' (demonstration only)'))
       WHERE claims IS NULL""",
    """UPDATE content SET indication = CASE measure
         WHEN 'diabetes' THEN 'Adults with type 2 diabetes on long-term medication'
         WHEN 'hypertension' THEN 'Adults with high blood pressure on long-term medication'
         WHEN 'cholesterol' THEN 'Adults with high cholesterol on statin therapy'
         ELSE 'Adults on long-term medication for a chronic condition' END
       WHERE indication IS NULL""",
    "UPDATE content SET safety_info = '{}' WHERE safety_info IS NULL".format(
        SAFETY.replace("'", "''")
    ),
    f"UPDATE content SET labelling_note = '{LABELLING}' WHERE labelling_note IS NULL",
    # The updated cohort summary revises the first summary of the same topic.
    """UPDATE content SET
         previous_id = (SELECT p.content_id FROM content p
                        WHERE p.topic = content.topic AND p.version = 1
                          AND p.title NOT LIKE '%updated%' ORDER BY p.content_id LIMIT 1),
         lineage_id = (SELECT p.content_id FROM content p
                       WHERE p.topic = content.topic AND p.version = 1
                         AND p.title NOT LIKE '%updated%' ORDER BY p.content_id LIMIT 1)
       WHERE version = 2 AND title LIKE '%updated%' AND previous_id IS NULL""",
    """UPDATE content_review SET
         content_version = (SELECT c.version FROM content c
                            WHERE c.content_id = content_review.content_id),
         resulting_status = CASE decision WHEN 'approve' THEN 'approved'
                                          ELSE 'rejected' END
       WHERE content_version IS NULL""",
    # Earlier decisions had one comment; for a rejection it was the reason for the author.
    "UPDATE content_review SET feedback = comment WHERE decision <> 'approve' AND feedback IS NULL",
    """UPDATE content SET decided_at = (SELECT MAX(r.ts) FROM content_review r
                                        WHERE r.content_id = content.content_id)
       WHERE decided_at IS NULL""",
    """UPDATE content SET approved_by_user_id = (
         SELECT r.reviewer_user_id FROM content_review r
         WHERE r.content_id = content.content_id AND r.decision = 'approve'
         ORDER BY r.ts DESC LIMIT 1)
       WHERE mlr_status = 'approved' AND approved_by_user_id IS NULL""",
)

# One approved version per lineage: older approved versions are superseded.
SUPERSEDED = """SELECT c.content_id, c.version, n.content_id FROM content c
  JOIN content n ON n.lineage_id = c.lineage_id AND n.version > c.version
                AND n.mlr_status = 'approved'
  WHERE c.mlr_status = 'approved'"""


def upgrade() -> None:
    with op.batch_alter_table("content") as batch:
        batch.alter_column("mlr_status", type_=sa.String(length=20))
        batch.add_column(sa.Column("lineage_id", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("previous_id", sa.String(length=16), nullable=True))
        batch.add_column(
            sa.Column("origin", sa.String(length=16), nullable=False, server_default="library")
        )
        batch.add_column(sa.Column("author_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("submitted_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("claims", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("indication", sa.Text(), nullable=True))
        batch.add_column(sa.Column("safety_info", sa.Text(), nullable=True))
        batch.add_column(sa.Column("labelling_note", sa.Text(), nullable=True))
        batch.add_column(sa.Column("jurisdictions", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("decided_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("review_owner_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("approved_by_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("withdrawn_reason", sa.Text(), nullable=True))
        batch.create_index("ix_content_lineage_id", ["lineage_id"])
        batch.create_foreign_key("fk_content_author", "user", ["author_user_id"], ["id"])
        batch.create_foreign_key("fk_content_owner", "user", ["review_owner_user_id"], ["id"])
        batch.create_foreign_key("fk_content_approver", "user", ["approved_by_user_id"], ["id"])
    with op.batch_alter_table("content_review") as batch:
        batch.alter_column("decision", type_=sa.String(length=20))
        batch.add_column(sa.Column("content_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("previous_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("resulting_status", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("medical", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("legal", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("regulatory", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("feedback", sa.Text(), nullable=True))
        batch.add_column(sa.Column("seen_by_author_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("interaction") as batch:
        batch.add_column(sa.Column("draft_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_interaction_draft", "message_draft", ["draft_id"], ["id"])
    op.create_table(
        "content_message",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lineage_id", sa.String(length=16), nullable=False),
        sa.Column(
            "content_id", sa.String(length=16), sa.ForeignKey("content.content_id"), nullable=False
        ),
        sa.Column("author_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("author_role", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("hcp_id", sa.String(length=16), nullable=True),
        sa.Column("interaction_id", sa.Integer(), sa.ForeignKey("interaction.id"), nullable=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_content_message_lineage_id", "content_message", ["lineage_id"])
    op.create_index("ix_content_message_content_id", "content_message", ["content_id"])
    op.create_index("ix_content_message_hcp_id", "content_message", ["hcp_id"])
    op.create_table(
        "content_message_read",
        sa.Column(
            "message_id", sa.Integer(), sa.ForeignKey("content_message.id"), primary_key=True
        ),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
    )
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        return  # the back-fills below are written for the POC's SQLite database
    for statement in BACKFILL:
        op.execute(statement)
    for old, version, newer in bind.execute(sa.text(SUPERSEDED)).fetchall():
        bind.execute(
            sa.text("UPDATE content SET mlr_status = 'superseded' WHERE content_id = :c"),
            {"c": old},
        )
        expired = bind.execute(
            sa.text(
                "SELECT id FROM nba WHERE content_id = :c "
                "AND status IN ('ready_for_review', 'approved')"
            ),
            {"c": old},
        ).fetchall()
        reason = f"Content {old} was superseded by {newer}"
        bind.execute(
            sa.text(
                "UPDATE nba SET status = 'expired', block_reason = :r WHERE content_id = :c "
                "AND status IN ('ready_for_review', 'approved')"
            ),
            {"c": old, "r": reason},
        )
        bind.execute(
            sa.text(
                "INSERT INTO audit_log (ts, entity_type, entity_id, action, actor, actor_role, "
                "reason, detail) VALUES (CURRENT_TIMESTAMP, 'content', :c, 'content_superseded', "
                "'system', 'system', :r, :d)"
            ),
            {
                "c": old,
                "r": reason,
                "d": json.dumps(
                    {
                        "version": version,
                        "previous_status": "approved",
                        "resulting_status": "superseded",
                        "superseded_by": newer,
                        "recommendations_expired": [r[0] for r in expired],
                    }
                ),
            },
        )


def downgrade() -> None:
    op.drop_table("content_message_read")
    op.drop_index("ix_content_message_hcp_id", "content_message")
    op.drop_index("ix_content_message_content_id", "content_message")
    op.drop_index("ix_content_message_lineage_id", "content_message")
    op.drop_table("content_message")
    with op.batch_alter_table("interaction") as batch:
        batch.drop_constraint("fk_interaction_draft", type_="foreignkey")
        batch.drop_column("draft_id")
    with op.batch_alter_table("content_review") as batch:
        for col in (
            "seen_by_author_at", "feedback", "regulatory", "legal", "medical",
            "resulting_status", "previous_status", "content_version",
        ):  # fmt: skip
            batch.drop_column(col)
    with op.batch_alter_table("content") as batch:
        batch.drop_constraint("fk_content_approver", type_="foreignkey")
        batch.drop_constraint("fk_content_owner", type_="foreignkey")
        batch.drop_constraint("fk_content_author", type_="foreignkey")
        batch.drop_index("ix_content_lineage_id")
        for col in (
            "withdrawn_reason", "approved_by_user_id", "review_owner_user_id", "decided_at",
            "jurisdictions", "labelling_note", "safety_info", "indication", "claims",
            "submitted_at", "author_user_id", "origin", "previous_id", "lineage_id",
        ):  # fmt: skip
            batch.drop_column(col)
