"""Accounts: sign-up, one-time code, sign-in, sign-out, assignments, administration."""

import secrets
from datetime import timedelta

import jwt
import pytest
from conftest import ADMIN_LOGIN, auth, new_session, sign_in
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import cycle
from app.auth import otp as otp_module
from app.auth.otp import EmailOtpSender, otp_service
from app.core.config import get_settings
from app.core.db import get_db
from app.core.permissions import ROLE_PERMISSIONS
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import AuditLog, CareManagerPatient, OtpChallenge, Patient, RepHcp, User
from app.models.tables import utcnow

# Generated per run. Satisfies the sign-up policy: 8+ characters, a letter and a digit.
PASSWORD = f"Pw-{secrets.token_urlsafe(8)}7"


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=41, n_patients=240, n_hcps=45, n_reps=4, n_care_managers=3))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app), db
    app.dependency_overrides.clear()
    db.close()


def register(client, email, role="patient", name="Jordan Lee", password=PASSWORD, confirm=None):
    return client.post(
        "/api/auth/signup",
        json={
            "name": name,
            "email": email,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
            "role": role,
        },
    )


def verify(client, challenge: dict, code: str | None = None):
    return client.post(
        "/api/auth/verify-otp",
        json={
            "verification_token": challenge["verification_token"],
            "code": code or challenge["dev_otp"],
        },
    )


def join(client, email, role, name="Jordan Lee") -> dict:
    """Registers and verifies; returns the session payload."""
    challenge = register(client, email, role, name)
    assert challenge.status_code == 201, challenge.text
    done = verify(client, challenge.json())
    assert done.status_code == 200, done.text
    return done.json()


def bearer(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['access_token']}"}


def login(client, email, password=PASSWORD):
    return client.post("/api/auth/login", data={"username": email, "password": password})


def code_of(response) -> str:
    return response.json()["detail"]["code"]


# --- Sign-up validation -------------------------------------------------------------


def test_signup_roles_exclude_administrator(env):
    client, _ = env
    roles = [r["role"] for r in client.get("/api/auth/roles").json()]
    assert roles == ["care_manager", "medical_rep", "compliance", "patient", "hcp"]


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"role": "admin"}, "invalid_role"),
        ({"role": "superuser"}, "invalid_role"),
        ({"confirm": "Different#2026"}, "password_mismatch"),
        ({"password": "short1"}, "weak_password"),
        ({"password": "lettersonly"}, "weak_password"),
        ({"email": "not-an-email"}, "invalid_email"),
        ({"name": "   "}, "invalid_name"),
    ],
)
def test_signup_rejects_bad_input(env, overrides, code):
    client, db = env
    before = db.scalar(select(func.count()).select_from(User))
    args = {"email": "bad.input@example.org", **overrides}
    response = register(client, **args)
    assert response.status_code == 422 and code_of(response) == code
    assert db.scalar(select(func.count()).select_from(User)) == before


def test_signup_creates_pending_account_without_a_session(env):
    client, db = env
    response = register(client, "Pending.Person@Example.org", "patient")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "pending.person@example.org"  # normalised
    assert body["delivery"] == "development" and len(body["dev_otp"]) == 6
    assert "access_token" not in body
    user = db.scalar(select(User).where(User.email == "pending.person@example.org"))
    assert (user.status, user.verified, user.source, user.patient_id) == (
        "pending", False, "signup", None,
    )  # fmt: skip
    assert user.password_hash.startswith("scrypt$") and PASSWORD not in user.password_hash
    challenge = db.scalar(select(OtpChallenge).where(OtpChallenge.user_id == user.id))
    assert body["dev_otp"] not in challenge.code_hash  # only a hash is stored

    # The verification token is not a session, and the account cannot sign in yet.
    probe = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {body['verification_token']}"}
    )
    assert probe.status_code == 401
    refused = login(client, "pending.person@example.org")
    assert refused.status_code == 403 and code_of(refused) == "verification_required"
    assert "access_token" not in refused.json()["detail"]
    assert refused.json()["detail"]["verification_token"]


def test_duplicate_email_is_refused_for_registered_and_seeded_accounts(env):
    client, _ = env
    again = register(client, "pending.person@example.org", "hcp")
    assert again.status_code == 409 and code_of(again) == "email_exists"
    for existing in ("ADMIN@admin.com", "cm01@nba.demo"):
        taken = register(client, existing, "patient")
        assert taken.status_code == 409 and code_of(taken) == "email_exists"


# --- One-time code -------------------------------------------------------------------


def test_wrong_code_counts_attempts_then_locks(env):
    client, db = env
    challenge = register(client, "locked.out@example.org").json()
    wrong = "000000" if challenge["dev_otp"] != "000000" else "111111"
    limit = get_settings().otp_max_attempts
    for remaining in range(limit - 1, 0, -1):
        response = verify(client, challenge, wrong)
        assert response.status_code == 400 and code_of(response) == "otp_incorrect"
        assert f"{remaining} attempts left" in response.json()["detail"]["message"]
    locked = verify(client, challenge, wrong)
    assert locked.status_code == 429 and code_of(locked) == "otp_locked"
    # The code is gone: even the right one no longer works.
    assert code_of(verify(client, challenge)) == "otp_missing"
    user = db.scalar(select(User).where(User.email == "locked.out@example.org"))
    assert not user.verified
    assert db.scalar(select(OtpChallenge).where(OtpChallenge.user_id == user.id)) is None


def test_expired_code_is_refused_and_resend_issues_a_new_one(env, monkeypatch):
    client, db = env
    challenge = register(client, "too.slow@example.org").json()
    early = client.post(
        "/api/auth/resend-otp", json={"verification_token": challenge["verification_token"]}
    )
    assert early.status_code == 429 and code_of(early) == "otp_resend_wait"

    later = utcnow() + timedelta(minutes=get_settings().otp_ttl_minutes, seconds=1)
    monkeypatch.setattr(otp_service, "_now", lambda: later)
    expired = verify(client, challenge)
    assert expired.status_code == 400 and code_of(expired) == "otp_expired"

    resent = client.post(
        "/api/auth/resend-otp", json={"verification_token": challenge["verification_token"]}
    )
    assert resent.status_code == 200 and resent.json()["dev_otp"]
    assert verify(client, resent.json()).status_code == 200


def test_code_is_single_use_and_removed_after_success(env):
    client, db = env
    challenge = register(client, "once.only@example.org").json()
    assert verify(client, challenge).status_code == 200
    user = db.scalar(select(User).where(User.email == "once.only@example.org"))
    assert user.verified and user.status == "active"
    assert db.scalar(select(OtpChallenge).where(OtpChallenge.user_id == user.id)) is None
    reused = verify(client, challenge)
    assert reused.status_code == 409 and code_of(reused) == "already_verified"


class FakeMail:
    channel = "email"

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, user, code, minutes):
        if self.fail:
            raise ConnectionError("mail server unreachable")
        self.sent.append((user.email, code, minutes))


def test_with_mail_configured_the_code_is_emailed_and_never_returned(env, monkeypatch):
    client, _ = env
    mail = FakeMail()
    monkeypatch.setattr(otp_module, "get_sender", lambda settings=None: mail)
    body = register(client, "mailed@example.org").json()
    assert body["delivery"] == "email" and "dev_otp" not in body
    (to, code, minutes) = mail.sent[0]
    assert to == "mailed@example.org" and len(code) == 6 and minutes == 10
    assert verify(client, body, code).status_code == 200


def test_mail_failure_falls_back_only_in_demo_mode(env, monkeypatch):
    client, db = env
    monkeypatch.setattr(otp_module, "get_sender", lambda settings=None: FakeMail(fail=True))
    fallback = register(client, "fallback@example.org").json()
    assert fallback["delivery"] == "development" and fallback["dev_otp"]

    monkeypatch.setattr(get_settings(), "demo_mode", False)
    strict = register(client, "strict@example.org")
    assert strict.status_code == 502 and code_of(strict) == "otp_delivery_failed"


def test_email_message_content():
    settings = get_settings().model_copy(update={"smtp_from": "no-reply@nba.example"})
    user = User(email="someone@example.org", display_name="Someone")
    message = EmailOtpSender(settings).build(user, "123456", 10)
    assert message["To"] == "someone@example.org" and message["From"] == "no-reply@nba.example"
    assert "123456" in message["Subject"] and "expires in 10 minutes" in message.get_content()


# --- Sign-in, session, sign-out ---------------------------------------------------------


def test_admin_signs_in_with_the_fixed_credentials(env):
    client, _ = env
    response = login(client, *ADMIN_LOGIN)
    assert response.status_code == 200
    user = response.json()["user"]
    assert (user["role"], user["home"]) == ("admin", "/dashboard")
    assert "user:manage" in user["permissions"] and "content:approve" not in user["permissions"]


def test_full_flow_signup_verify_use_logout_login(env):
    client, _ = env
    session = join(client, "full.flow@example.org", "care_manager", "Casey Morgan")
    assert session["user"]["home"] == "/queue" and session["user"]["name"] == "Casey Morgan"
    queue = client.get("/api/nba", headers=bearer(session))
    assert queue.status_code == 200 and queue.json()["total"] > 0

    assert client.post("/api/auth/logout", headers=bearer(session)).status_code == 200
    # Signed out on the server: the old token is dead, not merely forgotten by the browser.
    assert client.get("/api/nba", headers=bearer(session)).status_code == 401

    again = login(client, "FULL.flow@example.org")
    assert again.status_code == 200 and again.json()["user"]["role"] == "care_manager"
    assert client.get("/api/nba", headers=bearer(again.json())).status_code == 200
    assert login(client, "full.flow@example.org", "WrongPass1").status_code == 401


def test_role_in_a_token_is_ignored(env):
    """A client cannot promote itself: the role is read from the account, never the token."""
    client, db = env
    session = join(client, "sneaky@example.org", "patient")
    user = db.scalar(select(User).where(User.email == "sneaky@example.org"))
    settings = get_settings()
    claims = jwt.decode(session["access_token"], settings.jwt_secret, algorithms=["HS256"])
    forged = jwt.encode(
        {**claims, "role": "admin", "permissions": ["user:manage"]},
        settings.jwt_secret,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {forged}"}
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "patient"
    for path in ("/api/admin/users", "/api/patients", "/api/audit", "/api/admin/config"):
        assert client.get(path, headers=headers).status_code == 403
    # Signed with another key, or naming another account's version: not accepted at all.
    other_key = jwt.encode(claims, "some-other-secret", algorithm="HS256")
    stale = jwt.encode({**claims, "ver": user.token_version + 1}, settings.jwt_secret, "HS256")
    for token in (other_key, stale):
        assert (
            client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code
            == 401
        )


# --- Automatic assignment per role --------------------------------------------------


def test_new_patient_gets_one_unused_record_under_their_name(env):
    client, db = env
    session = join(client, "new.patient@example.org", "patient", "Riya Sharma")
    pid = session["user"]["patient_id"]
    assert pid and pid not in {f"PAT_{n:05d}" for n in range(1, 7)}  # not a seeded hero
    owners = db.scalars(select(User).where(User.patient_id == pid)).all()
    assert len(owners) == 1

    profile = client.get("/api/me/profile", headers=bearer(session)).json()
    assert (profile["patient_id"], profile["name"]) == (pid, "Riya Sharma")
    assert profile["therapies"] and "risk_score" not in profile["therapies"][0]
    # Own data only.
    for path in ("/api/patients", f"/api/patients/{pid}", "/api/patients/PAT_00002", "/api/hcps"):
        assert client.get(path, headers=bearer(session)).status_code == 403
    assert client.get("/api/me/consents", headers=bearer(session)).status_code == 200
    # Staff see the same record under the same name.
    seen = client.get(f"/api/patients/{pid}", headers=auth(client, "admin")).json()
    assert seen["name"] == "Riya Sharma"


def test_second_patient_gets_a_different_record(env):
    client, _ = env
    first = join(client, "p.one@example.org", "patient")["user"]["patient_id"]
    second = join(client, "p.two@example.org", "patient")["user"]["patient_id"]
    assert first and second and first != second


def test_new_hcp_gets_own_record_without_commercial_fields(env):
    client, _ = env
    session = join(client, "new.hcp@example.org", "hcp", "Anita Rao")
    assert session["user"]["hcp_id"] and session["user"]["home"] == "/inbox"
    profile = client.get("/api/me/profile", headers=bearer(session)).json()
    assert profile["name"] == "Dr. Anita Rao"
    assert not {"segment", "value_score", "rx_volume_annual"} & set(profile)
    assert client.get("/api/me/patients", headers=bearer(session)).status_code == 200
    assert client.get("/api/hcps", headers=bearer(session)).status_code == 403


def test_new_care_manager_gets_a_panel_and_sees_only_it(env):
    client, db = env
    session = join(client, "new.cm@example.org", "care_manager")
    user = db.scalar(select(User).where(User.email == "new.cm@example.org"))
    panel = set(
        db.scalars(
            select(CareManagerPatient.patient_id).where(
                CareManagerPatient.care_manager_user_id == user.id
            )
        )
    )
    assert len(panel) == get_settings().signup_panel_patients
    risks = set(db.scalars(select(Patient.risk_segment).where(Patient.patient_id.in_(panel))))
    assert risks == {"high", "medium", "low"}
    listed = client.get("/api/patients", params={"limit": 200}, headers=bearer(session)).json()
    assert {p["patient_id"] for p in listed["items"]} == panel
    outside = db.scalar(select(Patient.patient_id).where(Patient.patient_id.not_in(panel)))
    assert client.get(f"/api/patients/{outside}", headers=bearer(session)).status_code == 404
    assert client.get("/api/hcps", headers=bearer(session)).status_code == 403
    queue = client.get("/api/nba", params={"limit": 200}, headers=bearer(session)).json()
    assert queue["items"] and all(
        i["target_type"] == "PATIENT" and i["target_id"] in panel for i in queue["items"]
    )


def test_seeded_assignments_are_untouched_by_signups(env):
    client, db = env
    cm01 = db.scalar(select(User).where(User.username == "cm01"))
    before = db.scalar(
        select(func.count())
        .select_from(CareManagerPatient)
        .where(CareManagerPatient.care_manager_user_id == cm01.id)
    )
    join(client, "another.cm@example.org", "care_manager")
    after = db.scalar(
        select(func.count())
        .select_from(CareManagerPatient)
        .where(CareManagerPatient.care_manager_user_id == cm01.id)
    )
    assert before == after > 0


def test_new_rep_gets_hcps_and_no_patients(env):
    client, db = env
    session = join(client, "new.rep@example.org", "medical_rep")
    user = db.scalar(select(User).where(User.email == "new.rep@example.org"))
    mine = set(db.scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == user.id)))
    assert len(mine) == get_settings().signup_panel_hcps
    listed = client.get("/api/hcps", params={"limit": 200}, headers=bearer(session)).json()
    assert {h["hcp_id"] for h in listed["items"]} == mine
    assert client.get("/api/patients", headers=bearer(session)).status_code == 403
    assert client.get("/api/patients/PAT_00001", headers=bearer(session)).status_code == 403
    content = client.get("/api/content", headers=bearer(session)).json()
    assert content and all(c["usable"] and c["audience"] == "HCP" for c in content)


def test_new_compliance_account_is_limited_to_compliance_functions(env):
    client, _ = env
    session = join(client, "new.mlr@example.org", "compliance")
    assert session["user"]["home"] == "/content"
    allowed = ("/api/content", "/api/audit", "/api/nba", "/api/analytics/overview")
    denied = ("/api/patients", "/api/hcps", "/api/admin/users", "/api/admin/config")
    assert all(client.get(p, headers=bearer(session)).status_code == 200 for p in allowed)
    assert all(client.get(p, headers=bearer(session)).status_code == 403 for p in denied)
    items = client.get("/api/nba", headers=bearer(session)).json()["items"]
    assert all(i["target_name"] is None for i in items)


# --- Administration ------------------------------------------------------------------


def account_id(db, email) -> int:
    return db.scalar(select(User.id).where(User.email == email))


def test_only_admin_manages_users(env):
    client, db = env
    uid = account_id(db, "new.cm@example.org")
    for username in ("cm01", "rep01", "compliance1", "pat00001", "hcp0001"):
        headers = auth(client, username)
        assert client.get("/api/admin/users", headers=headers).status_code == 403
        assert client.get(f"/api/admin/users/{uid}", headers=headers).status_code == 403
        patch = client.patch(
            f"/api/admin/users/{uid}/status", json={"status": "disabled"}, headers=headers
        )
        put = client.put(
            f"/api/admin/users/{uid}/assignments", json={"patient_ids": []}, headers=headers
        )
        assert patch.status_code == put.status_code == 403
    listing = client.get(
        "/api/admin/users", params={"source": "signup"}, headers=auth(client, "admin")
    ).json()
    emails = {u["email"] for u in listing["items"]}
    assert {"new.cm@example.org", "new.rep@example.org", "pending.person@example.org"} <= emails
    assert all("password" not in key for u in listing["items"] for key in u)


def test_changing_assignments_changes_scope_but_never_role_or_permissions(env):
    client, db = env
    admin = auth(client, "admin")
    uid = account_id(db, "new.cm@example.org")
    before = client.get(f"/api/admin/users/{uid}", headers=admin).json()
    assert before["assignment"] == {"kind": "patients", "count": 30}

    new_panel = ["PAT_00001", "PAT_00002", "PAT_00003"]
    changed = client.put(
        f"/api/admin/users/{uid}/assignments", json={"patient_ids": new_panel}, headers=admin
    )
    assert changed.status_code == 200, changed.text
    after = changed.json()
    assert [p["patient_id"] for p in after["patients"]] == new_panel
    assert (after["role"], after["permissions"]) == (before["role"], before["permissions"])
    assert after["permissions"] == sorted(ROLE_PERMISSIONS["care_manager"])

    session = login(client, "new.cm@example.org").json()
    listed = client.get("/api/patients", headers=bearer(session)).json()
    assert [p["patient_id"] for p in listed["items"]] == new_panel
    assert session["user"]["role"] == "care_manager"
    log = db.scalar(select(AuditLog).where(AuditLog.action == "assignments_changed"))
    assert log.actor == "admin" and log.detail["after"]["count"] == 3


@pytest.mark.parametrize(
    ("email", "body", "status", "code"),
    [
        ("new.cm@example.org", {"hcp_ids": ["HCP_0001"]}, 422, "invalid_assignment"),
        ("new.cm@example.org", {"patient_ids": ["PAT_99999"]}, 422, "unknown_record"),
        ("new.rep@example.org", {"patient_ids": ["PAT_00001"]}, 422, "invalid_assignment"),
        ("new.patient@example.org", {"patient_id": "PAT_00001"}, 409, "record_in_use"),
        ("new.mlr@example.org", {"patient_ids": []}, 409, "no_assignments_for_role"),
    ],
)
def test_assignment_edits_are_validated(env, email, body, status, code):
    client, db = env
    response = client.put(
        f"/api/admin/users/{account_id(db, email)}/assignments",
        json=body,
        headers=auth(client, "admin"),
    )
    assert response.status_code == status and code_of(response) == code


def test_account_without_a_linked_record_gets_a_clear_answer(env):
    client, db = env
    admin = auth(client, "admin")
    session = join(client, "unlinked@example.org", "patient")
    uid = account_id(db, "unlinked@example.org")
    cleared = client.put(
        f"/api/admin/users/{uid}/assignments", json={"patient_id": None}, headers=admin
    )
    assert cleared.status_code == 200 and cleared.json()["assignment"]["count"] == 0
    for path in ("/api/me/profile", "/api/me/consents", "/api/me/inbox"):
        response = client.get(path, headers=bearer(session))
        assert response.status_code == 409 and code_of(response) == "no_assignment"


def test_disable_signs_the_account_out_and_blocks_sign_in(env):
    client, db = env
    admin = auth(client, "admin")
    session = join(client, "to.disable@example.org", "medical_rep")
    uid = account_id(db, "to.disable@example.org")
    off = client.patch(f"/api/admin/users/{uid}/status", json={"status": "disabled"}, headers=admin)
    assert off.status_code == 200 and off.json()["status"] == "disabled"
    assert off.json()["role"] == "medical_rep"
    assert client.get("/api/nba", headers=bearer(session)).status_code == 401
    refused = login(client, "to.disable@example.org")
    assert refused.status_code == 403 and code_of(refused) == "account_disabled"

    on = client.patch(f"/api/admin/users/{uid}/status", json={"status": "active"}, headers=admin)
    assert on.status_code == 200 and login(client, "to.disable@example.org").status_code == 200


def test_status_change_guards(env):
    client, db = env
    admin = auth(client, "admin")
    me = account_id(db, "admin@admin.com")
    own = client.patch(f"/api/admin/users/{me}/status", json={"status": "disabled"}, headers=admin)
    assert own.status_code == 409 and code_of(own) == "cannot_change_self"
    pending = account_id(db, "pending.person@example.org")
    early = client.patch(
        f"/api/admin/users/{pending}/status", json={"status": "active"}, headers=admin
    )
    assert early.status_code == 409 and code_of(early) == "not_verified"
    assert client.get("/api/admin/users/999999", headers=admin).status_code == 404


def test_registered_accounts_survive_a_demo_reset(env):
    client, db = env
    reset = client.post(
        "/api/admin/reset", json={"patients": 80, "hcps": 16}, headers=auth(client, "admin")
    )
    assert reset.status_code == 200, reset.text
    client.__dict__["_tokens"].clear()

    back = login(client, "new.cm@example.org")
    assert back.status_code == 200
    listed = client.get("/api/patients", headers=bearer(back.json())).json()
    assert [p["patient_id"] for p in listed["items"]] == ["PAT_00001", "PAT_00002", "PAT_00003"]
    hcp = login(client, "new.hcp@example.org").json()
    assert client.get("/api/me/profile", headers=bearer(hcp)).json()["name"] == "Dr. Anita Rao"
    # Seeded demo accounts and the fixed administrator are rebuilt as before.
    assert sign_in(client, "cm01") and sign_in(client, "admin")
    assert login(client, "pending.person@example.org").status_code == 403
