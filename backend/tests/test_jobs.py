"""Background jobs (app/jobs.py, app/worker.py) and per-cycle summary snapshots
(app/analytics/snapshots.py)."""

import os
import sqlite3
import threading
from datetime import timedelta

import pytest
from conftest import ApiClient, auth, engine_job, new_session, run_jobs
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import cycle, jobs, panel_adherence, pipeline
from app.analytics import measures, outcomes, snapshots
from app.core import clock
from app.core.db import get_db, make_engine
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import (
    AuditLog,
    Consent,
    EngineCycle,
    Hcp,
    Job,
    Nba,
    NbaCandidate,
    SummarySnapshot,
    User,
)
from app.models.enums import ConsentPurpose
from app.models.tables import utcnow

GEN = GenConfig(seed=29, n_patients=180, n_hcps=30, n_reps=3, n_care_managers=2)


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GEN)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def call(env, role, method, path, **kw):
    client, _ = env
    return client.request(method, path, headers=auth(client, role), **kw)


def factory(db):
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)


# --- Submission, execution, results ----------------------------------------------------------


def test_submission_returns_at_once_and_does_not_run_the_cycle(env):
    _, db = env
    cycles_before = db.scalar(select(func.count()).select_from(EngineCycle))
    response = call(env, "admin", "POST", "/api/admin/cycle")
    assert response.status_code == 202
    job = response.json()["job"]
    assert job["status"] == "queued" and job["kind"] == "cycle"
    assert db.scalar(select(func.count()).select_from(EngineCycle)) == cycles_before
    # A second engine operation while one is queued is refused with the active job.
    second = call(env, "admin", "POST", "/api/admin/play-out")
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "job_active"
    assert second.json()["detail"]["job"]["id"] == job["id"]
    # Reset and bulk send cannot start alongside it either.
    assert call(env, "admin", "POST", "/api/admin/bulk-send").status_code == 409
    assert call(env, "admin", "POST", "/api/admin/reset", json={}).status_code == 409
    run_jobs(db)
    done = call(env, "admin", "GET", f"/api/admin/jobs/{job['id']}").json()
    assert done["status"] == "succeeded" and done["error"] is None
    assert {"cycle_id", "as_of_date", "stats"} <= set(done["result"])
    assert done["result"]["stats"]["drafts"] > 0
    log = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "cycle_requested",
            AuditLog.entity_id == str(done["result"]["cycle_id"]),
        )
    )
    assert log is not None and log.actor_role == "admin"
    # The lock was released: a new submission is accepted.
    engine_job(env, call(env, "admin", "POST", "/api/admin/cycle"))


def test_only_engine_operators_may_submit_or_read_jobs(env):
    for role in ("rep01", "cm01", "pat00001", "hcp0001"):
        assert call(env, role, "POST", "/api/admin/cycle").status_code == 403
        assert call(env, role, "GET", "/api/admin/jobs").status_code == 403
        assert call(env, role, "GET", "/api/admin/jobs/1").status_code == 403


def test_job_cycle_matches_the_synchronous_cycle():
    """Same data, same day: the job path (committed in steps) writes exactly the same
    recommendations, candidates and counts as the original single-transaction cycle."""
    a, b = new_session(), new_session()
    try:
        _compare_paths(a, b)
    finally:
        a.close()
        b.close()


def _compare_paths(a, b):
    for db in (a, b):
        generate(db, GEN)
        db.commit()
    result_a = cycle.run(a)
    a.commit()
    result_b = cycle.run_in_steps(b)

    def recs(db, cycle_id):
        return sorted(
            (n.target_type, n.target_id, n.action, n.channel, n.content_id, n.status,
             n.scheduled_for, str(n.reason_codes), n.score)
            for n in db.scalars(select(Nba).where(Nba.cycle_id == cycle_id))
        )  # fmt: skip

    assert recs(a, result_a.cycle.id) == recs(b, result_b.cycle.id)
    assert {k: v for k, v in result_a.stats.items()} == {k: v for k, v in result_b.stats.items()}
    count = select(func.count()).select_from(NbaCandidate)
    assert a.scalar(count) == b.scalar(count)
    # The step path also published its summaries.
    assert b.scalar(select(func.count()).select_from(SummarySnapshot).where(
        SummarySnapshot.cycle_id == result_b.cycle.id
    ))  # fmt: skip


def test_failed_job_is_marked_with_a_sanitised_error_and_releases_the_lock(env, monkeypatch):
    _, db = env

    def boom(db, job, progress):
        raise RuntimeError("SELECT secret FROM patient WHERE name='Jane Roe'")

    monkeypatch.setitem(jobs.HANDLERS, "cycle", boom)
    response = call(env, "admin", "POST", "/api/admin/cycle")
    run_jobs(db)
    job = db.get(Job, response.json()["job"]["id"])
    assert job.status == "failed" and job.lock_key is None and job.finished_at
    assert job.error == "RuntimeError (see the worker log)"
    assert "Jane" not in job.error
    # Not retried: running the worker again leaves it failed.
    run_jobs(db)
    db.refresh(job)
    assert job.status == "failed"


def test_abandoned_jobs_are_marked_interrupted_never_rerun(env):
    _, db = env
    old = utcnow() - jobs.STALE_AFTER - timedelta(minutes=1)
    job = Job(
        kind="cycle", status="running", params={}, lock_key=jobs.ENGINE_LOCK,
        requested_at=old, started_at=old, heartbeat_at=old, worker_id="dead-worker",
    )  # fmt: skip
    db.add(job)
    db.commit()
    assert call(env, "admin", "POST", "/api/admin/cycle").status_code == 409
    assert jobs.recover_stale(db) == 1
    db.refresh(job)
    assert job.status == "interrupted" and job.lock_key is None and "stopped" in job.error
    cycles = db.scalar(select(func.count()).select_from(EngineCycle))
    run_jobs(db)
    assert db.scalar(select(func.count()).select_from(EngineCycle)) == cycles
    assert call(env, "admin", "POST", "/api/admin/cycle").status_code == 202
    run_jobs(db)


def test_a_job_is_claimed_once(env):
    _, db = env
    admin = db.scalar(select(User).where(User.username == "admin"))
    job = jobs.submit(db, "cycle", admin, {})
    f = factory(db)
    with f() as s1, f() as s2:
        first = jobs.claim_next(s1, "w1")
        second = jobs.claim_next(s2, "w2")
    assert first is not None and first.id == job.id and second is None
    jobs.execute(f, job.id)
    db.expire_all()
    assert db.get(Job, job.id).status == "succeeded"


def test_play_out_job_returns_the_previous_contract(env):
    result = engine_job(env, call(env, "admin", "POST", "/api/admin/play-out"))["result"]
    assert {"as_of_date", "responses", "cycle_id", "cycle"} <= set(result)


def test_worker_tick_runs_queued_jobs(env):
    from app import worker

    _, db = env
    admin = db.scalar(select(User).where(User.username == "admin"))
    job = jobs.submit(db, "cycle", admin, {})
    assert worker.tick(factory(db), "tick-worker") == 1
    db.expire_all()
    assert db.get(Job, job.id).status == "succeeded"


# --- Snapshots -----------------------------------------------------------------------------


def latest(db):
    return db.scalar(select(func.max(EngineCycle.id)))


def test_snapshots_equal_live_figures(env):
    client, db = env
    engine_job(env, call(env, "admin", "POST", "/api/admin/cycle"))
    settings = pipeline.engine_settings(db)
    today = clock.get_today(db)
    pts = panel_adherence.points_from_db(db, today, settings)
    for h in db.scalars(select(Hcp).where(Hcp.origin == "synthetic").limit(5)):
        live = panel_adherence.panel(h, panel_adherence.attributed_ids(db, h.hcp_id), pts, settings)
        assert snapshots.panel_for(db, h.hcp_id) == live
    since = outcomes.since_param(None, today)
    import json

    from fastapi.encoders import jsonable_encoder

    live = jsonable_encoder(outcomes.what_worked(db, "PATIENT", "situation", "all", since))
    assert json.dumps(
        snapshots.learning_for(db, "PATIENT", "situation", "all"), sort_keys=True
    ) == (json.dumps(live, sort_keys=True))
    live_measures = jsonable_encoder(measures.measures(db, today, settings, None))
    assert snapshots.measures_all(db) == live_measures
    # The API answers the same with the snapshot in place.
    api = call(env, "admin", "GET", "/api/analytics/learning").json()
    assert api == json.loads(json.dumps(live))


def test_consent_change_sends_panel_reads_back_to_live(env):
    _, db = env
    hcp_id = db.scalar(select(Hcp.hcp_id).where(Hcp.origin == "synthetic"))
    assert snapshots.panel_for(db, hcp_id) is not None
    consent = db.scalar(select(Consent).where(Consent.purpose == ConsentPurpose.AGGREGATE_INSIGHTS))
    db.add(
        Consent(
            patient_id=consent.patient_id, purpose=ConsentPurpose.AGGREGATE_INSIGHTS,
            channel=None, granted=not consent.granted, effective_from=clock.get_today(db),
            source="patient_portal",
        )
    )  # fmt: skip
    db.commit()
    assert snapshots.panel_for(db, hcp_id) is None


def test_failed_publication_leaves_no_partial_snapshot_and_readers_go_live(env, monkeypatch):
    _, db = env

    def broken(*args, **kwargs):
        raise RuntimeError("summary failed")

    monkeypatch.setattr(outcomes, "what_worked", broken)
    engine_job(env, call(env, "admin", "POST", "/api/admin/cycle"))
    cycle_id = latest(db)
    assert not db.scalar(
        select(func.count())
        .select_from(SummarySnapshot)
        .where(SummarySnapshot.cycle_id == cycle_id)
    )
    # Older snapshots belong to an older cycle and are not used.
    hcp_id = db.scalar(select(Hcp.hcp_id).where(Hcp.origin == "synthetic"))
    assert snapshots.panel_for(db, hcp_id) is None
    monkeypatch.undo()
    assert call(env, "admin", "GET", "/api/analytics/learning").status_code == 200


def test_snapshot_from_another_day_is_not_used(env):
    _, db = env
    engine_job(env, call(env, "admin", "POST", "/api/admin/cycle"))
    hcp_id = db.scalar(select(Hcp.hcp_id).where(Hcp.origin == "synthetic"))
    assert snapshots.panel_for(db, hcp_id) is not None
    with clock.override(clock.get_today(db) + timedelta(days=1)):
        assert snapshots.panel_for(db, hcp_id) is None


# --- SQLite concurrency settings ---------------------------------------------------------------


def test_file_sqlite_uses_wal_and_waits_for_a_busy_writer(tmp_path):
    path = tmp_path / "wal.db"
    engine = make_engine(f"sqlite:///{path}")
    with engine.connect() as conn:
        mode = conn.exec_driver_sql("PRAGMA journal_mode").scalar()
        timeout = conn.exec_driver_sql("PRAGMA busy_timeout").scalar()
        conn.exec_driver_sql("CREATE TABLE t (x INTEGER)")
        conn.commit()
    assert mode == "wal" and timeout >= 5000
    # A writer holds the lock briefly; a second writer waits instead of failing.
    holder = sqlite3.connect(path, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO t VALUES (1)")
    release = threading.Timer(0.5, holder.commit)
    release.start()
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO t VALUES (2)")
    # And readers were never blocked by the writer.
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM t").scalar() == 2
    holder.close()
    engine.dispose()


def test_memory_sqlite_and_other_databases_get_no_file_pragmas():
    engine = make_engine("sqlite://")
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "memory"
    engine.dispose()
    if os.environ.get("NBA_TEST_DATABASE_URL", "").startswith("postgresql"):
        pg = make_engine(os.environ["NBA_TEST_DATABASE_URL"])
        with pg.connect() as conn:
            assert conn.exec_driver_sql("SELECT 1").scalar() == 1
        pg.dispose()
