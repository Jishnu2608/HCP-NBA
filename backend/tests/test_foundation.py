from datetime import date

import pytest
from conftest import auth

from app.auth.service import ensure_system_admin
from app.core import clock
from app.core.db import Base

EXPECTED_TABLES = {
    "hcp", "patient", "patient_hcp", "user", "rep_hcp", "care_manager_patient",
    "patient_therapy", "medication_fill", "adherence_snapshot", "consent",
    "content", "content_review", "engine_cycle", "nba", "nba_candidate", "message_draft",
    "interaction", "feature_snapshot", "audit_log", "model_version", "engine_config",
    "sim_latent", "otp_challenge", "user_session", "invitation", "rate_limit_hit",
    "consent_record", "privacy_request", "patient_number", "patient_condition",
    "care_request", "care_note",
}  # fmt: skip


def test_schema_has_all_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_account_and_invitation_ids_are_never_reused(db):
    """Deleting the newest account must not hand its id (and its audit history) to the
    next one."""
    from app.models import User

    def add(name: str) -> int:
        user = User(username=name, email=f"{name}@example.org", display_name=name,
                    password_hash="x", role="patient")  # fmt: skip
        db.add(user)
        db.flush()
        return user.id

    add("first")
    newest = add("second")
    db.delete(db.get(User, newest))
    db.flush()
    assert add("third") > newest
    for table in ("user", "invitation"):
        assert Base.metadata.tables[table].dialect_options["sqlite"]["autoincrement"]


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_meta_reports_counts_and_clock(client, db):
    clock.set_today(db, date(2026, 6, 30))
    assert client.get("/api/meta").status_code == 401  # not public
    ensure_system_admin(db)
    db.commit()
    body = client.get("/api/meta", headers=auth(client, "admin")).json()
    assert body["as_of_date"] == "2026-06-30"
    assert body["row_counts"]["engine_config"] == 1
    assert body["row_counts"]["patient"] == 0


def test_clock_advance(db):
    clock.set_today(db, date(2026, 6, 30))
    assert clock.advance(db, 7) == date(2026, 7, 7)
    assert clock.get_today(db) == date(2026, 7, 7)
    with pytest.raises(ValueError):
        clock.advance(db, 0)
