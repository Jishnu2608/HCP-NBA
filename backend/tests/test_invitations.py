"""Invitation-based onboarding of professional roles, checked against a malicious client."""

from datetime import timedelta

import pytest
from conftest import (
    ApiClient,
    accept,
    as_user,
    auth,
    challenge_of,
    invite,
    new_session,
    onboard,
    signed_in,
    token_of,
    verify,
)
from sqlalchemy import func, select

from app import cycle
from app.auth import invitations as invitation_module
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import AuditLog, Invitation, User
from app.models.tables import utcnow

# Seeded accounts for each role.
SEEDED = {
    "admin": "admin",
    "hcp": "hcp0001",
    "medical_rep": "rep01",
    "care_manager": "cm01",
    "compliance": "compliance1",
    "patient": "pat00001",
}
ALLOWED = {
    ("admin", "hcp"), ("admin", "medical_rep"), ("admin", "care_manager"),
    ("admin", "compliance"), ("hcp", "medical_rep"), ("hcp", "care_manager"),
}  # fmt: skip
INVITABLE = ("hcp", "medical_rep", "care_manager", "compliance")


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=51, n_patients=200, n_hcps=40, n_reps=4, n_care_managers=3))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def code_of(response) -> str:
    return response.json()["detail"]["code"]


# --- Authority matrix through the API ------------------------------------------------------


@pytest.mark.parametrize("inviter", list(SEEDED))
@pytest.mark.parametrize("target", [*INVITABLE, "admin", "patient"])
def test_authority_matrix_is_enforced_by_the_api(env, inviter, target):
    """Every inviter role against every target role, sent straight to the API as a hand-made
    request (no UI involved). Only the six allowed pairs succeed."""
    client, db = env
    email = f"matrix.{inviter}.{target}@example.org"
    response = invite(client, auth(client, SEEDED[inviter]), email, target)
    if (inviter, target) in ALLOWED:
        assert response.status_code == 201, response.text
        assert response.json()["invitation"]["role"] == target
    elif target in ("admin", "patient") and inviter in ("admin", "hcp"):
        assert response.status_code == 422 and code_of(response) == "invalid_role"
    else:
        assert response.status_code == 403
    if (inviter, target) not in ALLOWED:
        assert db.scalar(select(Invitation).where(Invitation.email == email)) is None


def test_hcp_cannot_invite_mlr_even_with_a_crafted_request(env):
    client, db = env
    response = client.post(
        "/api/invitations",
        json={"email": "sneaky.mlr@example.org", "role": "compliance"},
        headers=auth(client, "hcp0001"),
    )
    assert response.status_code == 403 and code_of(response) == "invite_forbidden"
    assert db.scalar(select(Invitation).where(Invitation.email == "sneaky.mlr@example.org")) is None


def test_options_mirror_the_authority(env):
    client, _ = env
    roles = lambda who: [o["role"] for o in client.get(  # noqa: E731
        "/api/invitations/options", headers=auth(client, who)).json()]  # fmt: skip
    assert roles("admin") == ["hcp", "medical_rep", "care_manager", "compliance"]
    assert roles("hcp0001") == ["medical_rep", "care_manager"]
    for who in ("rep01", "cm01", "compliance1", "pat00001"):
        assert roles(who) == []
        assert client.get("/api/invitations", headers=auth(client, who)).status_code == 403


def test_invitation_body_cannot_carry_extra_authority(env):
    client, _ = env
    response = client.post(
        "/api/invitations",
        json={"email": "x@example.org", "role": "medical_rep", "invited_by_user_id": 1},
        headers=auth(client, "hcp0001"),
    )
    assert response.status_code == 422 and code_of(response) == "invalid_request"


# --- Email --------------------------------------------------------------------------------


class Mailbox:
    def __init__(self):
        self.sent = []

    def deliver(self, message):
        self.sent.append(message)


def test_invitation_email_content_and_no_token_in_the_response(env, monkeypatch):
    client, db = env
    box = Mailbox()
    monkeypatch.setattr(invitation_module, "get_mailer", lambda: box)
    response = invite(client, auth(client, "hcp0001"), "john.rep@example.org", "medical_rep")
    assert response.status_code == 201
    body = response.json()
    assert "dev_link" not in body and "token" not in str(body).lower()
    assert body["invitation"]["delivery"] == "email"

    (message,) = box.sent
    assert message["To"] == "john.rep@example.org"
    inviter = db.scalar(select(User).where(User.username == "hcp0001"))
    assert inviter.display_name in message["Subject"]
    text = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    for part in (text, html):
        assert "Medical Representative" in part and "Healthcare Professional" in part
        assert "john.rep@example.org" in part and "/invite/" in part
        assert "can only be used once" in part
    assert "Accept invitation" in html and "copy and paste" in html
    # The link works, and only a hash of its token is stored.
    token = text.split("/invite/")[1].split()[0]
    inv = db.scalar(select(Invitation).where(Invitation.email == "john.rep@example.org"))
    assert token not in inv.token_hash and len(inv.token_hash) == 64
    assert client.post("/api/invitations/lookup", json={"token": token}).status_code == 200


def test_failed_email_is_reported_to_the_inviter(env, monkeypatch):
    client, _ = env

    class Down:
        def deliver(self, message):
            raise ConnectionError("down")

    monkeypatch.setattr(invitation_module, "get_mailer", lambda: Down())
    response = invite(client, auth(client, "admin"), "unlucky@example.org", "hcp")
    assert response.status_code == 201
    assert response.json()["invitation"]["delivery"] == "failed"
    assert "dev_link" not in response.json()


# --- Acceptance --------------------------------------------------------------------------------


def test_full_invitation_flow_sets_role_lineage_and_badge(env):
    client, db = env
    sent = invite(client, auth(client, "admin"), "sarah.hcp@example.org", "hcp")
    token = token_of(sent)
    info = client.post("/api/invitations/lookup", json={"token": token}).json()
    assert info["role"] == "hcp" and info["role_label"] == "Healthcare Professional"
    assert info["email"] == "sarah.hcp@example.org" and info["inviter_name"] == "Administrator"

    accepted = accept(client, token, name="Sarah Williams")
    assert accepted.status_code == 201, accepted.text
    # Opening and filling in the form does not accept the invitation; the code does.
    inv = db.scalar(select(Invitation).where(Invitation.email == "sarah.hcp@example.org"))
    assert inv.status == "pending"
    user = db.scalar(select(User).where(User.email == "sarah.hcp@example.org"))
    assert (user.role, user.status, user.professionally_verified) == ("hcp", "pending", False)

    session = signed_in(verify(client, challenge_of(accepted)))
    db.refresh(inv)
    db.refresh(user)
    assert inv.status == "accepted" and inv.accepted_at is not None
    assert session["user"]["role"] == "hcp" and session["user"]["professionally_verified"]
    assert session["user"]["verification_source"] == "invitation"
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert user.invited_by_user_id == admin.id and user.source == "invitation"

    # Sarah (HCP) invites John (MR): lineage Admin -> Sarah -> John.
    sent = invite(client, as_user(session), "john.mr@example.org", "medical_rep")
    assert sent.status_code == 201, sent.text
    john = signed_in(
        verify(client, challenge_of(accept(client, token_of(sent), name="John Smith")))
    )
    assert john["user"]["role"] == "medical_rep" and john["user"]["professionally_verified"]
    jid = john["user"]["id"]
    detail = client.get(f"/api/admin/users/{jid}", headers=auth(client, "admin")).json()
    assert [p["name"] for p in detail["lineage"]] == [
        "Administrator",
        "Sarah Williams",
        "John Smith",
    ]
    assert detail["invited_by"]["name"] == "Sarah Williams"
    # Sarah sees the invitation she sent, and only hers.
    mine = client.get("/api/invitations", headers=as_user(session)).json()
    assert {i["email"] for i in mine["items"]} == {"john.mr@example.org"}
    assert mine["items"][0]["accepted_user"]["name"] == "John Smith"


def test_invited_role_and_email_cannot_be_changed_by_the_recipient(env):
    client, db = env
    token = token_of(
        invite(client, auth(client, "admin"), "fixed.role@example.org", "care_manager")
    )
    for forged in ({"role": "admin"}, {"email": "other@example.org"},
                   {"professionally_verified": True}, {"is_minor": False}):  # fmt: skip
        response = accept(client, token, **forged)
        assert response.status_code == 422 and code_of(response) == "invalid_request"
    session = signed_in(verify(client, challenge_of(accept(client, token))))
    assert session["user"]["role"] == "care_manager"
    assert session["user"]["email"] == "fixed.role@example.org"


def test_invitation_is_single_use(env):
    client, _ = env
    token = token_of(invite(client, auth(client, "admin"), "single.use@example.org", "hcp"))
    challenge = challenge_of(accept(client, token))
    assert verify(client, challenge).status_code == 200
    again = accept(client, token)
    assert again.status_code == 409 and code_of(again) == "invitation_used"
    looked = client.post("/api/invitations/lookup", json={"token": token})
    assert code_of(looked) == "invitation_used"


def test_expired_invitation(env, monkeypatch):
    client, db = env
    token = token_of(invite(client, auth(client, "admin"), "late@example.org", "hcp"))
    later = utcnow() + timedelta(hours=73)
    monkeypatch.setattr(invitation_module, "_now", lambda: later)
    response = accept(client, token)
    assert response.status_code == 410 and code_of(response) == "invitation_expired"
    assert response.json()["detail"]["inviter_name"] == "Administrator"
    assert token not in response.text
    inv = db.scalar(select(Invitation).where(Invitation.email == "late@example.org"))
    assert inv.status == "expired"
    assert db.scalar(
        select(AuditLog).where(
            AuditLog.action == "invitation_expired", AuditLog.entity_id == str(inv.id)
        )
    )


def test_unknown_or_broken_token(env):
    client, _ = env
    for token in ("not-a-real-token", "x" * 128):
        response = client.post("/api/invitations/lookup", json={"token": token})
        assert response.status_code == 404 and code_of(response) == "invitation_invalid"


def test_reissue_revokes_the_old_link(env):
    client, db = env
    admin = auth(client, "admin")
    first = invite(client, admin, "reissue.me@example.org", "medical_rep")
    old_token, inv_id = token_of(first), first.json()["invitation"]["id"]
    again = client.post(f"/api/invitations/{inv_id}/reissue", headers=admin)
    assert again.status_code == 200
    new_token = again.json()["dev_link"].rsplit("/", 1)[1]
    assert code_of(client.post("/api/invitations/lookup", json={"token": old_token})) == (
        "invitation_revoked"
    )
    assert client.post("/api/invitations/lookup", json={"token": new_token}).status_code == 200
    pending = db.scalar(
        select(func.count()).select_from(Invitation).where(
            Invitation.email == "reissue.me@example.org", Invitation.status == "pending"
        )
    )  # fmt: skip
    assert pending == 1  # never two working links for the same person


def test_new_invitation_supersedes_own_pending_one_but_not_someone_elses(env):
    client, _ = env
    first = invite(client, auth(client, "hcp0001"), "shared.target@example.org", "care_manager")
    assert first.status_code == 201
    # Another HCP cannot override it.
    other = invite(client, auth(client, "hcp0002"), "shared.target@example.org", "care_manager")
    assert other.status_code == 409 and code_of(other) == "invitation_pending"
    # The admin can; the earlier link stops working.
    admin = invite(client, auth(client, "admin"), "shared.target@example.org", "medical_rep")
    assert admin.status_code == 201
    assert code_of(client.post("/api/invitations/lookup", json={"token": token_of(first)})) == (
        "invitation_revoked"
    )


def test_inviter_cannot_manage_someone_elses_invitation(env):
    client, _ = env
    sent = invite(client, auth(client, "admin"), "admins.own@example.org", "medical_rep")
    inv_id = sent.json()["invitation"]["id"]
    hcp = auth(client, "hcp0001")
    assert client.post(f"/api/invitations/{inv_id}/revoke", headers=hcp).status_code == 404
    assert client.post(f"/api/invitations/{inv_id}/reissue", headers=hcp).status_code == 404
    for who in ("rep01", "pat00001"):
        r = client.post(f"/api/invitations/{inv_id}/revoke", headers=auth(client, who))
        assert r.status_code == 403


def test_revoked_between_acceptance_and_code(env):
    client, db = env
    admin = auth(client, "admin")
    sent = invite(client, admin, "too.late@example.org", "hcp")
    challenge = challenge_of(accept(client, token_of(sent)))
    client.post(f"/api/invitations/{sent.json()['invitation']['id']}/revoke", headers=admin)
    response = verify(client, challenge)
    assert response.status_code == 409 and code_of(response) == "invitation_unavailable"
    user = db.scalar(select(User).where(User.email == "too.late@example.org"))
    assert not user.verified and not user.professionally_verified


def test_disabled_inviter_invalidates_pending_invitations(env):
    client, db = env
    hcp = onboard(client, "short.lived.hcp@example.org", "hcp", "Lee Park")
    sent = invite(client, as_user(hcp), "their.rep@example.org", "medical_rep")
    token = token_of(sent)
    client.patch(
        f"/api/admin/users/{hcp['user']['id']}/status",
        json={"status": "disabled"},
        headers=auth(client, "admin"),
    )
    response = accept(client, token)
    assert response.status_code == 410 and code_of(response) == "invitation_revoked"


def test_existing_account_and_minor_professional(env):
    client, _ = env
    admin = auth(client, "admin")
    taken = invite(client, admin, "cm01@nba.demo", "medical_rep")
    assert taken.status_code == 409 and code_of(taken) == "account_exists"
    token = token_of(invite(client, admin, "teen.pro@example.org", "medical_rep"))
    today = utcnow().date()
    young = today.replace(year=today.year - 16, day=min(today.day, 28)).isoformat()
    response = accept(client, token, dob=young)
    assert response.status_code == 422 and code_of(response) == "age_requirement"


def test_acceptance_on_another_device_restarts_cleanly(env):
    client, db = env
    token = token_of(invite(client, auth(client, "admin"), "two.devices@example.org", "hcp"))
    accept(client, token, name="First Device")
    second = accept(client, token, name="Second Device")
    assert second.status_code == 201
    assert (
        db.scalar(
            select(func.count()).select_from(User).where(User.email == "two.devices@example.org")
        )
        == 1
    )
    session = signed_in(verify(client, challenge_of(second)))
    assert session["user"]["name"] == "Second Device"


def test_patient_signup_never_gets_the_badge(env):
    client, _ = env
    response = client.post(
        "/api/auth/signup",
        json={
            "name": "Pat Doe", "email": "pat.doe@example.org", "date_of_birth": "1970-01-01",
            "password": "Patient-pass-123", "confirm_password": "Patient-pass-123",
        },
    )  # fmt: skip
    session = signed_in(verify(client, challenge_of(response)))
    assert session["user"]["role"] == "patient" and not session["user"]["professionally_verified"]


def test_seeded_professionals_are_system_provisioned(env):
    _, db = env
    rep = db.scalar(select(User).where(User.username == "rep01"))
    patient = db.scalar(select(User).where(User.username == "pat00001"))
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert rep.professionally_verified and rep.verification_source == "system"
    assert not patient.professionally_verified and patient.date_of_birth is not None
    assert not admin.professionally_verified


def test_audit_trail_of_invitations(env):
    _, db = env
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.entity_type == "invitation")))
    expected = {
        "invitation_created", "invitation_claimed", "invitation_accepted",
        "invitation_revoked", "invitation_reissued", "invitation_expired",
    }  # fmt: skip
    assert expected <= actions
    # No raw token is ever written to the audit log.
    for detail in db.scalars(select(AuditLog.detail).where(AuditLog.entity_type == "invitation")):
        assert "token" not in str(detail).lower()
