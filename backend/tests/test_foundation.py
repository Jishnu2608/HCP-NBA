from datetime import date

import pytest

from app.core import clock
from app.core.db import Base

EXPECTED_TABLES = {
    "hcp", "patient", "patient_hcp", "user", "rep_hcp", "care_manager_patient",
    "patient_therapy", "medication_fill", "adherence_snapshot", "consent",
    "content", "content_review", "engine_cycle", "nba", "nba_candidate", "message_draft",
    "interaction", "feature_snapshot", "audit_log", "model_version", "engine_config",
    "sim_latent",
}  # fmt: skip


def test_schema_has_all_tables():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_meta_reports_counts_and_clock(client, db):
    clock.set_today(db, date(2026, 6, 30))
    body = client.get("/api/meta").json()
    assert body["as_of_date"] == "2026-06-30"
    assert body["row_counts"]["engine_config"] == 1
    assert body["row_counts"]["patient"] == 0


def test_clock_advance(db):
    clock.set_today(db, date(2026, 6, 30))
    assert clock.advance(db, 7) == date(2026, 7, 7)
    assert clock.get_today(db) == date(2026, 7, 7)
    with pytest.raises(ValueError):
        clock.advance(db, 0)
