"""F-13: reading never changes business records. Content detail, the inbox, specialty decisions
and the invitation list used to mark things read, seen or expired when opened; now every GET
leaves the business tables as they were, and explicit POSTs do the marking.

Bookkeeping tables (sessions, rate limits, the audit log with its access records) may change.
"""

from datetime import timedelta

import pytest
from conftest import ApiClient, auth, new_session
from context_world import table_hashes
from sqlalchemy import select

from app import cycle
from app.auth import invitations as invitation_module
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import Content, ContentMessage, ContentMessageRead, Invitation, User
from app.models.tables import utcnow

BOOKKEEPING = {"audit_log", "rate_limit_hit", "user_session", "user", "ai_generation", "job"}


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=53, n_patients=40, n_hcps=12, n_reps=2, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def _hashes(db):
    db.expire_all()
    return table_hashes(db, exclude=BOOKKEEPING)


def _unread_message(db, client):
    """A content message from the HCP to MLR, unread by compliance."""
    hcp = db.scalar(select(User).where(User.username == "hcp0001"))
    content = db.scalar(select(Content).where(Content.mlr_status == "approved").order_by(
        Content.content_id))  # fmt: skip
    m = ContentMessage(content_id=content.content_id, lineage_id=content.lineage_id,
                       author_user_id=hcp.id, author_role="hcp", hcp_id=hcp.hcp_id,
                       body="A question about this material.", ts=utcnow())  # fmt: skip
    db.add(m)
    db.commit()
    return content, m


def test_get_routes_change_no_business_record(env, monkeypatch):
    client, db = env
    content, _ = _unread_message(db, client)
    invite = client.post("/api/invitations", json={"email": "late.f13@example.org", "role": "hcp"},
                         headers=auth(client, "admin"))  # fmt: skip
    assert invite.status_code in (200, 201), invite.text
    later = utcnow() + timedelta(hours=80)
    monkeypatch.setattr(invitation_module, "_now", lambda: later)
    calls = [
        ("compliance1", f"/api/content/{content.content_id}"),
        ("hcp0001", "/api/me/inbox"),
        ("hcp0001", "/api/me/specialty-requests"),
        ("admin", "/api/invitations"),
    ]
    for who, _path in calls:
        auth(client, who)  # sign in first: sessions are bookkeeping
    before = _hashes(db)
    for who, path in calls:
        r = client.get(path, headers=auth(client, who))
        assert r.status_code == 200, (path, r.text)
    after = _hashes(db)
    changed = sorted(t for t in before if before[t] != after.get(t))
    assert changed == [], changed
    # The expired invitation reads as expired but is not rewritten by reading.
    listed = client.get("/api/invitations", headers=auth(client, "admin")).json()["items"]
    late = next(i for i in listed if i["email"] == "late.f13@example.org")
    assert late["status"] == "expired"
    db.expire_all()
    row = db.scalar(select(Invitation).where(Invitation.email == "late.f13@example.org"))
    assert row.status == "pending"


def test_explicit_posts_do_the_marking(env):
    client, db = env
    content, message = _unread_message(db, client)
    auth(client, "compliance1")  # reviewers are onboarded at their first sign-in
    reader = db.scalar(select(User).where(User.email == "compliance1@nba.demo"))

    def marks():
        return db.scalar(
            select(ContentMessageRead).where(
                ContentMessageRead.message_id == message.id,
                ContentMessageRead.user_id == reader.id,
            )
        )

    client.get(f"/api/content/{content.content_id}", headers=auth(client, "compliance1"))
    db.expire_all()
    assert marks() is None  # opening it marked nothing
    r = client.post(f"/api/content/{content.content_id}/messages/read",
                    headers=auth(client, "compliance1"))  # fmt: skip
    assert r.status_code == 200 and r.json()["marked"] >= 1
    db.expire_all()
    assert marks() is not None
    inbox = client.post("/api/me/inbox/read", headers=auth(client, "hcp0001"))
    assert inbox.status_code == 200
    seen = client.post("/api/me/specialty-requests/seen", headers=auth(client, "hcp0001"))
    assert seen.status_code == 200 and seen.json()["marked"] == 0
    # The marking routes keep the same scope as reading: unknown content is not found, and a
    # patient's inbox has nothing to mark.
    assert client.post("/api/content/NOPE/messages/read",
                       headers=auth(client, "compliance1")).status_code == 404  # fmt: skip
    assert client.post("/api/me/inbox/read",
                       headers=auth(client, "pat00001")).json()["marked"] == 0  # fmt: skip
