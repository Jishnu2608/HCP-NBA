"""invitations, server-side sessions, date of birth, professional verification

Adds the invitation and user_session tables and the user columns for date of birth,
professional verification and invitation lineage.

Back-fill on upgrade:
  * seeded professional accounts are marked professionally verified (source "system");
  * seeded patient accounts take the date of birth of their linked synthetic record;
  * any self-registered professional account (possible before invitations existed) is
    disabled, because professional roles may now only be granted by an invitation.

Revision ID: c3d9e5a7b1f2
Revises: a1f4c2e97b30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d9e5a7b1f2"
down_revision: str | None = "a1f4c2e97b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROFESSIONAL = "('hcp', 'medical_rep', 'care_manager', 'compliance')"


def upgrade() -> None:
    with op.batch_alter_table("user") as batch:
        batch.add_column(sa.Column("date_of_birth", sa.Date(), nullable=True))
        batch.add_column(
            sa.Column(
                "professionally_verified", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch.add_column(sa.Column("professionally_verified_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("verification_source", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("invited_by_user_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_user_invited_by_user_id_user", "user", ["invited_by_user_id"], ["id"]
        )

    op.create_table(
        "user_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("user_agent", sa.String(length=160), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="fk_user_session_user_id_user"),
        sa.PrimaryKeyConstraint("id", name="pk_user_session"),
        sa.UniqueConstraint("token_hash", name="uq_user_session_token_hash"),
    )
    op.create_index("ix_user_session_user_id", "user_session", ["user_id"])

    op.create_table(
        "invitation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("invited_by_user_id", sa.Integer(), nullable=False),
        sa.Column("inviter_role", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("delivery", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("replaced_by_id", sa.Integer(), nullable=True),
        sa.Column("claimed_user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"], ["user.id"], name="fk_invitation_invited_by_user_id_user"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by_user_id"], ["user.id"], name="fk_invitation_revoked_by_user_id_user"
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"], ["invitation.id"], name="fk_invitation_replaced_by_id_invitation"
        ),
        sa.ForeignKeyConstraint(
            ["claimed_user_id"], ["user.id"], name="fk_invitation_claimed_user_id_user"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitation"),
        sa.UniqueConstraint("token_hash", name="uq_invitation_token_hash"),
    )
    op.create_index("ix_invitation_email", "invitation", ["email"])
    op.create_index("ix_invitation_invited_by_user_id", "invitation", ["invited_by_user_id"])
    op.create_index("ix_invitation_status", "invitation", ["status"])

    op.execute(
        "UPDATE \"user\" SET professionally_verified = 1, verification_source = 'system', "
        "professionally_verified_at = CURRENT_TIMESTAMP "
        f"WHERE source = 'seed' AND role IN {PROFESSIONAL}"
    )
    op.execute(
        'UPDATE "user" SET date_of_birth = (SELECT birth_date FROM patient '
        'WHERE patient.patient_id = "user".patient_id) '
        "WHERE source = 'seed' AND patient_id IS NOT NULL"
    )
    op.execute(
        "INSERT INTO audit_log (ts, entity_type, entity_id, action, actor, actor_role, reason) "
        "SELECT CURRENT_TIMESTAMP, 'user', CAST(id AS TEXT), 'account_status_changed', "
        "'migration', 'system', 'Professional role without an invitation' "
        f"FROM \"user\" WHERE source = 'signup' AND role IN {PROFESSIONAL} "
        "AND status != 'disabled'"
    )
    op.execute(
        "UPDATE \"user\" SET status = 'disabled' "
        f"WHERE source = 'signup' AND role IN {PROFESSIONAL}"
    )


def downgrade() -> None:
    op.drop_index("ix_invitation_status", table_name="invitation")
    op.drop_index("ix_invitation_invited_by_user_id", table_name="invitation")
    op.drop_index("ix_invitation_email", table_name="invitation")
    op.drop_table("invitation")
    op.drop_index("ix_user_session_user_id", table_name="user_session")
    op.drop_table("user_session")
    with op.batch_alter_table("user") as batch:
        batch.drop_constraint("fk_user_invited_by_user_id_user", type_="foreignkey")
        for column in (
            "invited_by_user_id",
            "verification_source",
            "professionally_verified_at",
            "professionally_verified",
            "date_of_birth",
        ):
            batch.drop_column(column)
