"""Backend hardening: request limits, JSON and datetime behaviour (on whichever database the
suite runs: SQLite by default, PostgreSQL with NBA_TEST_DATABASE_URL), and time conventions."""

import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import ApiClient, auth, new_session
from sqlalchemy import select

from app.core import clock
from app.core.db import get_db
from app.core.http_security import MAX_BODY_BYTES
from app.main import app
from app.models import EngineCycle, Job, SummarySnapshot
from app.models.tables import utcnow

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
def env():
    db = new_session()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def test_oversized_bodies_are_refused(env):
    client, _ = env
    big = "x" * (MAX_BODY_BYTES + 10)
    declared = client.post(
        "/api/auth/login", content=big, headers={"Content-Type": "application/json"}
    )
    assert declared.status_code == 413
    assert declared.json()["detail"]["code"] == "payload_too_large"

    def chunks():
        for _ in range(5):
            yield b"x" * (MAX_BODY_BYTES // 2)

    streamed = client.post(
        "/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"}
    )
    assert streamed.status_code == 413
    # Normal-sized requests are untouched.
    small = client.post("/api/auth/login", json={"email": "a@example.org", "password": "x"})
    assert small.status_code == 401


VALUE = {
    "text": "Großes Ärztehaus – 東京 ✓",
    "int": 7,
    "float": 0.125,
    "bool": True,
    "none": None,
    "list": [1, "two", {"three": 3.5}],
    "nested": {"a": {"b": [False, None]}},
}


def test_json_columns_round_trip_exactly(env):
    _, db = env
    job = Job(kind="cycle", status="succeeded", params={}, result=VALUE, requested_at=utcnow())
    db.add(job)
    cycle = EngineCycle(as_of_date=date(2026, 10, 13))
    db.add(cycle)
    db.flush()
    db.add(
        SummarySnapshot(
            kind="k", scope_key="s", cycle_id=cycle.id, calc_version=1, as_of=date(2026, 10, 13),
            payload=[VALUE, [1, 2]],
        )
    )  # fmt: skip
    db.commit()
    db.expire_all()
    assert db.get(Job, job.id).result == VALUE
    assert db.scalar(select(SummarySnapshot.payload)) == [VALUE, [1, 2]]
    # A JSON null and an absent value stay distinct where the column says so.
    job.result = None
    db.commit()
    db.expire_all()
    assert db.get(Job, job.id).result is None


def test_instants_are_naive_utc_and_compare_correctly(env):
    _, db = env
    now = utcnow()
    assert now.tzinfo is None
    job = Job(kind="cycle", status="queued", params={}, requested_at=now)
    db.add(job)
    db.commit()
    db.expire_all()
    stored = db.get(Job, job.id).requested_at
    assert stored.tzinfo is None and abs((stored - now).total_seconds()) < 0.001
    earlier = db.scalar(select(Job.id).where(Job.requested_at < now + timedelta(seconds=1)))
    assert earlier == job.id


def test_application_code_never_creates_local_or_aware_wall_clock_times():
    """Stored instants are naive UTC from `tables.utcnow` (core/clock.py documents the time
    base). `datetime.now()` without UTC, `utcnow()` of datetime and `date.today()` would
    silently mix local time in."""
    pattern = re.compile(r"datetime\.now\(\)|datetime\.utcnow\(|date\.today\(|datetime\.today\(")
    offenders = [
        f"{p.relative_to(APP)}:{n}"
        for p in APP.rglob("*.py")
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line) and not line.strip().startswith("#")
    ]
    assert offenders == []


def test_api_dates_and_instants_keep_their_format(env):
    client, db = env
    from app.datagen.generate import GenConfig, generate

    generate(db, GenConfig(seed=3, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=2))
    db.commit()
    body = client.get("/api/admin/cycles", headers=auth(client, "admin")).json()
    assert body == []  # none yet
    from app import cycle

    cycle.run(db)
    db.commit()
    row = client.get("/api/admin/cycles", headers=auth(client, "admin")).json()[0]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["as_of_date"])
    # Instants: ISO 8601, UTC, no offset (the web client reads them as UTC).
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?", row["started_ts"])


# --- Sending the same recommendation twice ---------------------------------------------------


def _approved(db):
    from app import cycle
    from app.datagen.generate import GenConfig, generate
    from app.models import Nba, User
    from app.models.enums import NbaStatus

    generate(db, GenConfig(seed=8, n_patients=80, n_hcps=14, n_reps=2, n_care_managers=2))
    cycle.run(db)
    db.commit()
    admin = db.scalar(select(User).where(User.username == "admin"))
    nba = db.scalar(
        select(Nba).where(
            Nba.status == NbaStatus.READY_FOR_REVIEW,
            Nba.channel.in_(("email", "portal", "sms")),
            Nba.scheduled_for <= clock.today(),
        )
    )
    nba.status, nba.reviewed_by_user_id = NbaStatus.APPROVED, admin.id
    db.commit()
    return nba.id, admin.id


def test_a_stale_second_send_is_refused(env):
    from sqlalchemy import func
    from sqlalchemy.orm import sessionmaker

    from app.engagement import delivery
    from app.models import Interaction, Nba, User

    _, db = env
    nba_id, admin_id = _approved(db)
    other = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    stale = other.get(Nba, nba_id)  # still "approved" in this session's view
    delivery.send(db, db.get(Nba, nba_id), db.get(User, admin_id))
    db.commit()
    with pytest.raises(ValueError):
        delivery.send(other, stale, other.get(User, admin_id))
    other.rollback()
    count = db.scalar(
        select(func.count()).select_from(Interaction).where(Interaction.nba_id == nba_id)
    )
    assert count == 1


@pytest.mark.skipif(
    not __import__("os").environ.get("NBA_TEST_DATABASE_URL", "").startswith("postgresql"),
    reason="needs two real connections with row locks (PostgreSQL run)",
)
def test_concurrent_sends_deliver_exactly_once(env):
    import threading

    from sqlalchemy import func
    from sqlalchemy.orm import sessionmaker

    from app.engagement import delivery
    from app.models import Interaction, Nba, User

    _, db = env
    nba_id, admin_id = _approved(db)
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    barrier, outcomes = threading.Barrier(2), []

    def attempt():
        with factory() as s:
            nba, admin = s.get(Nba, nba_id), s.get(User, admin_id)
            barrier.wait()
            try:
                delivery.send(s, nba, admin)
                s.commit()
                outcomes.append("sent")
            except ValueError:
                s.rollback()
                outcomes.append("refused")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["refused", "sent"]
    count = db.scalar(
        select(func.count()).select_from(Interaction).where(Interaction.nba_id == nba_id)
    )
    assert count == 1
