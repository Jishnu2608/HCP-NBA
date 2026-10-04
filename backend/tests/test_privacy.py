"""Legal documents, consent records, re-acceptance, privacy requests, export, jurisdiction
and retention hygiene. Everything is checked on the server."""

import json
import re
from datetime import timedelta

import pytest
from conftest import (
    ADULT_DOB,
    PASSWORD,
    PATIENT_AGREEMENTS,
    ApiClient,
    accept,
    auth,
    challenge_of,
    cookie_header,
    invite,
    new_session,
    signed_in,
    token_of,
    verify,
)
from sqlalchemy import func, select

from app import cycle
from app.core import jurisdiction
from app.core.config import get_settings
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.legal import registry
from app.mail.templates import InvitationEmail, invitation_email, otp_email
from app.main import app
from app.maintenance import purge_expired
from app.models import AuditLog, ConsentRecord, PrivacyRequest, User, UserSession
from app.models.tables import utcnow


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=81, n_patients=120, n_hcps=24, n_reps=2, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def code_of(response) -> str:
    return response.json()["detail"]["code"]


def signup(client, email, **overrides):
    body = {
        "name": "Privacy Person", "email": email, "date_of_birth": ADULT_DOB,
        "password": PASSWORD, "confirm_password": PASSWORD, **PATIENT_AGREEMENTS, **overrides,
    }  # fmt: skip
    return client.post("/api/auth/signup", json=body)


def patient_session(client, email) -> dict:
    response = signup(client, email)
    assert response.status_code == 201, response.text
    return signed_in(verify(client, challenge_of(response)))


def as_(session) -> dict:
    return cookie_header(nba_session=session["token"])


# --- Agreements at sign-up -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"accept_terms": False}, "terms_required"),
        ({"consent_health_data": False}, "health_consent_required"),
        ({"country": "XX"}, "invalid_country"),
        ({"country": "US", "region": None}, "invalid_region"),
        ({"country": "DE", "region": "CA"}, "invalid_region"),
    ],
)
def test_signup_needs_explicit_agreements_and_residence(env, overrides, code):
    client, db = env
    before = db.scalar(select(func.count()).select_from(User))
    response = signup(client, "no.agreement@example.org", **overrides)
    assert response.status_code == 422 and code_of(response) == code
    assert db.scalar(select(func.count()).select_from(User)) == before


def test_agreements_have_no_default(env):
    """Leaving a box out of the request is not agreeing: the field is required."""
    client, _ = env
    body = {k: v for k, v in PATIENT_AGREEMENTS.items() if k != "accept_terms"}
    response = client.post(
        "/api/auth/signup",
        json={"name": "A B", "email": "omit@example.org", "date_of_birth": ADULT_DOB,
              "password": PASSWORD, "confirm_password": PASSWORD, **body},
    )  # fmt: skip
    assert response.status_code == 422 and code_of(response) == "invalid_request"


def test_signup_records_each_agreement_with_its_version(env):
    client, db = env
    session = patient_session(client, "recorded@example.org")
    assert session["user"]["pending_consents"] == []
    user = db.scalar(select(User).where(User.email == "recorded@example.org"))
    rows = db.scalars(select(ConsentRecord).where(ConsentRecord.user_id == user.id)).all()
    assert {(r.kind, r.version, r.action, r.jurisdiction, r.source) for r in rows} == {
        ("terms", "1.0", "accepted", "US-CA", "signup"),
        ("privacy_ack", "1.0", "accepted", "US-CA", "signup"),
        ("health_data", "1.0", "accepted", "US-CA", "signup"),
    }
    assert (user.country, user.region) == ("US", "CA")


# --- Re-acceptance ---------------------------------------------------------------------------


def test_new_required_version_blocks_until_accepted(env, monkeypatch):
    client, db = env
    session = patient_session(client, "reaccept@example.org")
    original = registry._raw
    monkeypatch.setitem(registry.VERSIONS, "terms", ["1.0", "1.1"])
    monkeypatch.setattr(
        registry,
        "_raw",
        lambda kind, version: original(kind, "1.0" if version == "1.1" else version),
    )
    me = client.get("/api/auth/me", headers=as_(session)).json()
    assert me["pending_consents"] == ["terms"]
    blocked = client.get("/api/me/profile", headers=as_(session))
    assert blocked.status_code == 403 and code_of(blocked) == "acceptance_required"
    # Reading the documents, the privacy page and signing out still work.
    assert client.get("/api/privacy/status", headers=as_(session)).status_code == 200
    assert client.get("/api/legal/documents/terms").json()["version"] == "1.1"
    done = client.post("/api/privacy/accept", json={"kinds": ["terms"]}, headers=as_(session))
    assert done.status_code == 200 and done.json()["pending"] == []
    assert client.get("/api/me/profile", headers=as_(session)).status_code == 200
    history = client.get("/api/privacy/history", headers=as_(session)).json()
    assert history[0]["kind"] == "terms" and history[0]["version"] == "1.1"
    assert history[0]["action"] == "accepted" and history[0]["source"] == "reacceptance"
    # The 1.0 acceptance is still there: history is never rewritten.
    assert any(h["kind"] == "terms" and h["version"] == "1.0" for h in history)


def test_seeded_accounts_must_accept_at_first_sign_in(env):
    client, _ = env
    response = client.post(
        "/api/auth/login", json={"email": "cm02@nba.demo", "password": _demo_password()}
    )
    assert set(response.json()["user"]["pending_consents"]) == {"terms", "privacy_ack"}
    blocked = client.get(
        "/api/nba", headers=cookie_header(nba_session=response.cookies["nba_session"])
    )
    assert code_of(blocked) == "acceptance_required"


def _demo_password() -> str:
    from conftest import DEMO_PASSWORD

    return DEMO_PASSWORD


def test_only_applicable_consents_can_be_accepted(env):
    client, _ = env
    staff = auth(client, "rep01")
    response = client.post("/api/privacy/accept", json={"kinds": ["health_data"]}, headers=staff)
    assert response.status_code == 422 and code_of(response) == "invalid_consent"
    forged = client.post(
        "/api/privacy/accept", json={"kinds": ["terms"], "version": "9.9"}, headers=staff
    )
    assert code_of(forged) == "invalid_request"


# --- Withdrawal ------------------------------------------------------------------------------


def test_withdrawing_health_consent_restricts_and_opens_a_request(env):
    client, db = env
    session = patient_session(client, "withdraw@example.org")
    done = client.post("/api/privacy/withdraw", json={"kind": "health_data"}, headers=as_(session))
    assert done.status_code == 200 and done.json()["pending"] == ["health_data"]
    assert code_of(client.get("/api/me/profile", headers=as_(session))) == "acceptance_required"
    user = db.scalar(select(User).where(User.email == "withdraw@example.org"))
    request = db.scalar(select(PrivacyRequest).where(PrivacyRequest.user_id == user.id))
    assert request.type == "restriction" and request.status == "submitted"
    # Giving it again is as easy as withdrawing.
    again = client.post(
        "/api/privacy/accept", json={"kinds": ["health_data"]}, headers=as_(session)
    )
    assert again.json()["pending"] == []
    assert client.get("/api/me/profile", headers=as_(session)).status_code == 200


def test_terms_cannot_be_withdrawn_in_place(env):
    client, _ = env
    response = client.post(
        "/api/privacy/withdraw", json={"kind": "terms"}, headers=auth(client, "rep01")
    )
    assert response.status_code == 422  # only health_data is withdrawable here


# --- Privacy requests ----------------------------------------------------------------------------


def test_privacy_requests_are_private_and_worked_by_privacy_managers(env):
    client, db = env
    alice = patient_session(client, "alice.req@example.org")
    bob = patient_session(client, "bob.req@example.org")
    made = client.post(
        "/api/privacy/requests",
        json={"type": "access", "details": "Please send everything."},
        headers=as_(alice),
    )
    assert made.status_code == 201 and made.json()["status"] == "submitted"
    request_id = made.json()["id"]
    assert all(
        r["id"] != request_id for r in client.get("/api/privacy/requests", headers=as_(bob)).json()
    )
    for who in ("rep01", "cm01", "compliance1", "hcp0001", "pat00001"):
        assert (
            client.get("/api/admin/privacy-requests", headers=auth(client, who)).status_code == 403
        )
    admin = auth(client, "admin")
    queue = client.get("/api/admin/privacy-requests", headers=admin).json()
    item = next(i for i in queue["items"] if i["id"] == request_id)
    assert item["requester"]["email"] == "alice.req@example.org"
    url = f"/api/admin/privacy-requests/{request_id}"
    assert code_of(client.patch(url, json={"status": "completed"}, headers=admin)) == (
        "resolution_required"
    )
    assert client.patch(url, json={"status": "in_review"}, headers=admin).status_code == 200
    closed = client.patch(
        url, json={"status": "completed", "resolution": "Export sent."}, headers=admin
    )
    assert closed.json()["status"] == "completed"
    again = client.patch(url, json={"status": "in_review"}, headers=admin)
    assert again.status_code == 409
    mine = client.get("/api/privacy/requests", headers=as_(alice)).json()
    assert mine[0]["status"] == "completed" and mine[0]["resolution"] == "Export sent."
    actions = set(
        db.scalars(select(AuditLog.action).where(AuditLog.entity_type == "privacy_request"))
    )
    assert {"privacy_request_submitted", "privacy_request_updated"} <= actions
    # The request text is not copied into the audit log.
    details = db.scalars(select(AuditLog.detail).where(AuditLog.entity_type == "privacy_request"))
    assert all("Please send everything" not in json.dumps(d) for d in details)


# --- Export -------------------------------------------------------------------------------------


def test_export_contains_only_your_own_data(env):
    client, db = env
    session = patient_session(client, "export.me@example.org")
    response = client.get("/api/privacy/export", headers=as_(session))
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    data = response.json()
    assert data["account"]["email"] == "export.me@example.org"
    assert data["patient"]["record"]["name"] == "Privacy Person"
    assert data["consent_history"] and "note" in data
    text = json.dumps(data)
    assert "risk_score" not in text and "risk_segment" not in text
    others = db.scalars(select(User.email).where(User.email != "export.me@example.org")).all()
    assert not any(email in text for email in others)
    hcp = client.get("/api/privacy/export", headers=auth(client, "hcp0001")).json()
    assert not {"segment", "value_score", "rx_volume_annual"} & set(hcp["hcp"]["record"])
    staff = client.get("/api/privacy/export", headers=auth(client, "rep01")).json()
    assert "patient" not in staff and "hcp" not in staff
    assert db.scalar(select(AuditLog).where(AuditLog.action == "data_exported"))


# --- Documents ---------------------------------------------------------------------------------


def test_documents_are_public_versioned_and_marked_draft(env):
    client, _ = env
    listing = client.get("/api/legal/documents").json()
    assert {d["kind"] for d in listing} == {"privacy", "terms", "cookies"}
    privacy = client.get("/api/legal/documents/privacy").json()
    assert privacy["draft"] and privacy["version"] == "1.0" and privacy["effective_date"]
    assert registry.TO_BE_CONFIRMED in json.dumps(privacy)
    assert "controller_name" in privacy["unconfirmed"]
    assert client.get("/api/legal/documents/privacy", params={"version": "0.1"}).status_code == 404
    assert client.get("/api/legal/documents/nope").status_code == 404


def test_operator_details_fill_the_placeholders(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(get_settings(), "legal_controller_name", "Example Health Ltd")
    doc = client.get("/api/legal/documents/terms").json()
    assert "Example Health Ltd" in json.dumps(doc)
    assert doc["draft"]  # still a draft until legal review sets the document flag


def test_no_document_claims_compliance_or_certification():
    claim = re.compile(
        r"\b(HIPAA|GDPR|CCPA|CPRA|SOC ?2|ISO ?27001)[- ](compliant|certified|compliance certified)"
        r"|fully compliant|100% compliant|guarantee(s|d)? (compliance|security)",
        re.I,
    )
    for path in registry.DOCUMENTS_DIR.glob("*.json"):
        assert not claim.search(path.read_text(encoding="utf-8")), path.name


def test_documents_describe_only_storage_that_exists():
    """The Cookie Policy lists exactly the cookies the server sets."""
    cookies = json.dumps(registry.load("cookies"))
    for name in ("nba_session", "nba_csrf", "nba_verify", "nba.theme", "nba.signup.challenge"):
        assert name in cookies


# --- Jurisdiction --------------------------------------------------------------------------------


def test_adult_age_follows_jurisdiction():
    assert jurisdiction.adult_age("US", "MS") == 21
    assert jurisdiction.adult_age("US", "AL") == 19
    assert jurisdiction.adult_age("US", "CA") == 18
    assert jurisdiction.adult_age("DE", None) == 18


def test_professional_age_uses_where_they_live(env):
    client, _ = env
    today = utcnow().date()
    twenty = today.replace(year=today.year - 20, day=min(today.day, 28)).isoformat()
    admin = auth(client, "admin")
    t1 = token_of(invite(client, admin, "young.ms@example.org", "medical_rep"))
    refused = accept(client, t1, dob=twenty, country="US", region="MS")
    assert refused.status_code == 422 and code_of(refused) == "age_requirement"
    t2 = token_of(invite(client, admin, "young.ca@example.org", "medical_rep"))
    assert accept(client, t2, dob=twenty, country="US", region="CA").status_code == 201


# --- Emails and retention ------------------------------------------------------------------------


def test_emails_carry_no_health_information():
    health_words = ("medication", "adherence", "refill", "prescription", "diagnos", "therapy")
    messages = [
        otp_email("a@example.org", "A", "123456", 10),
        invitation_email(InvitationEmail("a@example.org", "Admin", "Administrator",
                                         "Care Manager", "http://x/invite/t", utcnow())),
    ]  # fmt: skip
    for message in messages:
        text = message.get_body(("plain",)).get_content().lower()
        assert not any(word in text for word in health_words)


def test_purge_removes_only_expired_security_rows(env):
    _, db = env
    user = db.scalar(select(User).where(User.username == "admin"))
    now = utcnow()
    live = UserSession(token_hash="a" * 64, user_id=user.id, created_at=now, last_seen_at=now,
                       expires_at=now + timedelta(hours=1))  # fmt: skip
    old = UserSession(token_hash="b" * 64, user_id=user.id, created_at=now, last_seen_at=now,
                      expires_at=now - timedelta(seconds=1))  # fmt: skip
    db.add_all([live, old])
    db.flush()
    purge_expired(db)
    hashes = set(db.scalars(select(UserSession.token_hash)))
    assert "a" * 64 in hashes and "b" * 64 not in hashes
    assert db.scalar(select(func.count()).select_from(ConsentRecord)) > 0  # untouched
    db.rollback()


def test_unauthenticated_privacy_endpoints_are_refused(env):
    client, _ = env
    assert client.get("/api/privacy/status").status_code == 401
    assert client.get("/api/privacy/export").status_code == 401
