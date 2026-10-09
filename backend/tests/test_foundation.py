from datetime import UTC, date, datetime

from conftest import auth

from app.auth.service import ensure_system_admin
from app.core import clock
from app.core.db import Base

EXPECTED_TABLES = {
    "hcp", "patient", "patient_hcp", "user", "rep_hcp", "care_manager_patient",
    "patient_therapy", "medication_fill", "adherence_snapshot", "consent",
    "content", "content_review",
    "content_message", "content_message_read", "hcp_task", "care_plan", "care_plan_measure",
    "health_reading", "checkin", "coin_ledger",
    "leaderboard_profile", "engine_cycle", "nba", "nba_candidate",
    "message_draft",
    "interaction", "feature_snapshot", "audit_log", "model_version", "engine_config",
    "sim_latent", "otp_challenge", "user_session", "invitation", "rate_limit_hit",
    "consent_record", "privacy_request", "patient_number", "patient_condition",
    "care_request", "care_note", "hcp_specialty", "hcp_number", "specialty_change_request",
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


def utc_today() -> date:
    """Today is the UTC date: one time base for stored instants and date rules."""
    return datetime.now(UTC).date()


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_meta_reports_counts_and_the_real_date(client, db):
    assert client.get("/api/meta").status_code == 401  # not public
    ensure_system_admin(db)
    db.commit()
    body = client.get("/api/meta", headers=auth(client, "admin")).json()
    assert body["as_of_date"] == utc_today().isoformat()
    assert body["row_counts"]["patient"] == 0


def test_today_is_the_real_date_and_nothing_stores_a_demo_date(db):
    assert clock.get_today(db) == utc_today()
    with clock.override(date(2031, 1, 2)):
        assert clock.get_today(db) == date(2031, 1, 2)
    assert clock.get_today(db) == utc_today()
    assert not hasattr(clock, "set_today") and not hasattr(clock, "advance")
    clock.set_simulated_through(db, date(2026, 9, 30))
    # A processing marker for the simulator, never "today".
    assert clock.get_today(db) == utc_today()
