"""Accounts: patient sign-up, one-time code, sign-in, sign-out, assignments, administration.

Professional accounts are created through invitations here (see test_invitations.py for the
invitation rules themselves).
"""

from datetime import timedelta

import pytest
from conftest import (
    ADMIN_LOGIN,
    ADULT_DOB,
    PASSWORD,
    PATIENT_AGREEMENTS,
    ApiClient,
    as_user,
    auth,
    challenge_of,
    cookie_header,
    new_session,
    onboard,
    sign_in,
    signed_in,
    verify,
)
from conftest import login as login_as
from sqlalchemy import func, select

from app import cycle
from app.auth import otp as otp_module
from app.auth.otp import EmailOtpSender, otp_service
from app.core.config import get_settings
from app.core.db import get_db
from app.core.permissions import ROLE_PERMISSIONS
from app.datagen.generate import GenConfig, generate
from app.mail import transport
from app.main import app
from app.models import AuditLog, CareManagerPatient, OtpChallenge, Patient, RepHcp, User
from app.models.tables import utcnow


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=41, n_patients=240, n_hcps=45, n_reps=4, n_care_managers=3))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def register(
    client, email, name="Jordan Lee", password=PASSWORD, confirm=None, dob=ADULT_DOB, **extra
):
    return client.post(
        "/api/auth/signup",
        json={
            "name": name,
            "email": email,
            "date_of_birth": dob,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
            **PATIENT_AGREEMENTS,
            **extra,
        },
    )


def join(client, email, name="Jordan Lee", dob=ADULT_DOB) -> dict:
    """Registers a patient and verifies; returns the signed-in session."""
    response = register(client, email, name, dob=dob)
    assert response.status_code == 201, response.text
    return signed_in(verify(client, challenge_of(response)))


def login(client, email, password=PASSWORD):
    return login_as(client, email, password)


def code_of(response) -> str:
    return response.json()["detail"]["code"]


# --- Sign-up validation -------------------------------------------------------------


def test_public_signup_offers_no_roles(env):
    client, _ = env
    assert client.get("/api/auth/roles").status_code in (404, 405)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"confirm": "Different#2026x"}, "password_mismatch"),
        ({"password": "short1"}, "weak_password"),
        ({"password": "lettersonlyhere"}, "weak_password"),
        ({"email": "not-an-email"}, "invalid_email"),
        ({"name": "   "}, "invalid_name"),
        ({"dob": "2999-01-01"}, "invalid_dob"),
        ({"dob": "1990-02-30"}, "invalid_dob"),
        ({"dob": "1850-01-01"}, "invalid_dob"),
        ({"dob": "yesterday"}, "invalid_dob"),
    ],
)
def test_signup_rejects_bad_input(env, overrides, code):
    client, db = env
    before = db.scalar(select(func.count()).select_from(User))
    args = {"email": "bad.input@example.org", **overrides}
    response = register(client, **args)
    assert response.status_code == 422 and code_of(response) == code
    assert db.scalar(select(func.count()).select_from(User)) == before


@pytest.mark.parametrize(
    "forged",
    [
        {"role": "admin"},
        {"role": "compliance"},
        {"professionally_verified": True},
        {"is_minor": False},
        {"verified": True},
        {"status": "active"},
        {"permissions": ["user:manage"]},
        {"user_id": 1},
    ],
)
def test_signup_refuses_any_client_supplied_authority(env, forged):
    """The request cannot choose a role, a verification state or anything else the server
    decides: such fields are rejected outright."""
    client, db = env
    before = db.scalar(select(func.count()).select_from(User))
    response = register(client, "forger@example.org", **forged)
    assert response.status_code == 422 and code_of(response) == "invalid_request"
    assert db.scalar(select(func.count()).select_from(User)) == before


def test_signup_creates_a_pending_patient_without_a_session(env):
    client, db = env
    response = register(client, "Pending.Person@Example.org")
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "pending.person@example.org"  # normalised
    assert body["delivery"] == "development" and len(body["dev_otp"]) == 6
    assert "access_token" not in body and "verification_token" not in body
    assert response.cookies.get("nba_verify") and not response.cookies.get("nba_session")
    user = db.scalar(select(User).where(User.email == "pending.person@example.org"))
    assert (user.status, user.verified, user.source, user.role, user.patient_id) == (
        "pending", False, "signup", "patient", None,
    )  # fmt: skip
    assert user.date_of_birth.isoformat() == ADULT_DOB
    assert not user.professionally_verified
    assert user.password_hash.startswith("scrypt$") and PASSWORD not in user.password_hash
    challenge = db.scalar(select(OtpChallenge).where(OtpChallenge.user_id == user.id))
    assert body["dev_otp"] not in challenge.code_hash  # only a hash is stored

    # The verification cookie is not a session, and the account cannot sign in yet.
    probe = client.get(
        "/api/auth/me", headers=cookie_header(nba_session=response.cookies.get("nba_verify"))
    )
    assert probe.status_code == 401
    refused = login(client, "pending.person@example.org")
    assert refused.status_code == 403 and code_of(refused) == "verification_required"
    assert "verification_token" not in refused.json()["detail"]
    assert refused.cookies.get("nba_verify") and not refused.cookies.get("nba_session")


def test_existing_email_gets_the_same_answer_and_never_verifies(env, monkeypatch):
    """Sign-up cannot be used to find out which emails are registered."""
    client, db = env
    sent = []

    class Mailbox:
        def deliver(self, message):
            sent.append(message)

    monkeypatch.setattr("app.auth.service.get_mailer", lambda: Mailbox())
    fresh = register(client, "brand.new@example.org")
    taken = register(client, "cm01@nba.demo")
    assert fresh.status_code == taken.status_code == 201
    assert set(fresh.json()) == set(taken.json())
    assert taken.cookies.get("nba_verify")
    # The owner is told by email; nobody is created; no code ever works.
    assert [m["To"] for m in sent] == ["cm01@nba.demo"]
    assert db.scalar(select(func.count()).select_from(User).where(User.email == "cm01@nba.demo"))
    for _ in range(get_settings().otp_max_attempts):
        attempt = verify(client, challenge_of(taken))
        assert code_of(attempt) in ("otp_incorrect", "otp_locked")
    cm01 = db.scalar(select(User).where(User.email == "cm01@nba.demo"))
    assert cm01.role == "care_manager" and cm01.source == "seed"


def test_unfinished_signup_can_be_started_again(env):
    client, db = env
    first = register(client, "restart@example.org", name="First Name")
    again = register(client, "restart@example.org", name="Second Name")
    assert first.status_code == again.status_code == 201
    user = db.scalar(select(User).where(User.email == "restart@example.org"))
    assert user.display_name == "Second Name" and not user.verified
    assert verify(client, challenge_of(again)).status_code == 200


def test_minor_patient_may_register_and_is_flagged(env):
    client, db = env
    today = utcnow().date()
    dob = today.replace(year=today.year - 15, day=min(today.day, 28)).isoformat()
    session = join(client, "young.patient@example.org", dob=dob)
    assert session["user"]["role"] == "patient"
    assert "date_of_birth" not in session["user"] and "is_minor" not in session["user"]
    uid = db.scalar(select(User.id).where(User.email == "young.patient@example.org"))
    detail = client.get(f"/api/admin/users/{uid}", headers=auth(client, "admin")).json()
    assert detail["age_band"] == "minor" and "date_of_birth" not in detail


# --- One-time code -------------------------------------------------------------------


def test_wrong_code_counts_attempts_then_locks(env):
    client, db = env
    challenge = challenge_of(register(client, "locked.out@example.org"))
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
    failures = db.scalar(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "otp_failed", AuditLog.entity_id == str(user.id)
        )
    )  # fmt: skip
    assert failures == limit + 1  # every attempt, including the one after the lock


def test_expired_code_is_refused_and_resend_issues_a_new_one(env, monkeypatch):
    client, db = env
    challenge = challenge_of(register(client, "too.slow@example.org"))
    pending = cookie_header(nba_verify=challenge["_verify"])
    early = client.post("/api/auth/resend-otp", headers=pending)
    assert early.status_code == 429 and code_of(early) == "otp_resend_wait"

    later = utcnow() + timedelta(minutes=get_settings().otp_ttl_minutes, seconds=1)
    monkeypatch.setattr(otp_service, "_now", lambda: later)
    expired = verify(client, challenge)
    assert expired.status_code == 400 and code_of(expired) == "otp_expired"

    resent = client.post("/api/auth/resend-otp", headers=pending)
    assert resent.status_code == 200 and resent.json()["dev_otp"]
    assert verify(client, challenge_of(resent)).status_code == 200


def test_code_is_single_use_and_removed_after_success(env):
    client, db = env
    challenge = challenge_of(register(client, "once.only@example.org"))
    assert verify(client, challenge).status_code == 200
    user = db.scalar(select(User).where(User.email == "once.only@example.org"))
    assert user.verified and user.status == "active"
    assert db.scalar(select(OtpChallenge).where(OtpChallenge.user_id == user.id)) is None
    reused = verify(client, challenge)
    assert reused.status_code == 409 and code_of(reused) == "already_verified"


def test_verification_needs_the_cookie(env):
    client, _ = env
    response = client.post("/api/auth/verify-otp", json={"code": "123456"})
    assert response.status_code == 401 and code_of(response) == "verification_expired"


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
    response = register(client, "mailed@example.org")
    body = response.json()
    assert body["delivery"] == "email" and "dev_otp" not in body
    (to, code, minutes) = mail.sent[0]
    assert to == "mailed@example.org" and len(code) == 6 and minutes == 10
    assert verify(client, challenge_of(response), code).status_code == 200


def test_mail_failure_is_reported_never_shown_on_screen(env, monkeypatch):
    """With a mail server configured, a failed send is a failure even in demo mode: no code
    is stored, none is returned, and the account stays pending until a resend succeeds."""
    client, db = env
    mail = FakeMail(fail=True)
    monkeypatch.setattr(otp_module, "get_sender", lambda settings=None: mail)
    response = register(client, "unreachable@example.org")
    body = response.json()
    assert body["delivery"] == "failed" and "dev_otp" not in body
    assert body["expires_in"] == 0 and body["resend_in"] == 0
    user = db.scalar(select(User).where(User.email == "unreachable@example.org"))
    assert not user.verified and user.status == "pending"
    codes = select(func.count()).select_from(OtpChallenge).where(OtpChallenge.user_id == user.id)
    assert db.scalar(codes) == 0

    pending = cookie_header(nba_verify=response.cookies.get("nba_verify"))
    again = client.post("/api/auth/resend-otp", headers=pending)
    assert again.status_code == 502 and code_of(again) == "otp_delivery_failed"

    mail.fail = False
    resent = client.post("/api/auth/resend-otp", headers=pending)
    assert resent.json()["delivery"] == "email" and "dev_otp" not in resent.json()
    assert verify(client, challenge_of(response), mail.sent[-1][1]).status_code == 200


def test_email_message_content():
    settings = get_settings().model_copy(update={"smtp_from": "no-reply@nba.example"})
    user = User(email="someone@example.org", display_name="Someone")
    message = EmailOtpSender(settings).build(user, "123456", 10)
    assert message["To"] == "someone@example.org"
    assert "123456" in message["Subject"]
    text = message.get_body(("plain",)).get_content()
    html = message.get_body(("html",)).get_content()
    assert "expires in 10 minutes" in text and "123456" in html


def test_no_on_screen_code_outside_a_local_run(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(get_settings(), "environment", "production")
    monkeypatch.setattr(transport, "get_mailer", lambda settings=None: None)
    response = register(client, "hosted@example.org")
    assert response.status_code == 503 and code_of(response) == "otp_delivery_unavailable"


# --- Sign-in, session, sign-out ---------------------------------------------------------


def test_admin_signs_in_with_the_fixed_credentials(env):
    client, _ = env
    response = login(client, *ADMIN_LOGIN)
    assert response.status_code == 200
    user = response.json()["user"]
    assert (user["role"], user["home"]) == ("admin", "/dashboard")
    assert "user:manage" in user["permissions"] and "content:approve" not in user["permissions"]
    assert not user["professionally_verified"]


def test_full_flow_signup_verify_use_logout_login(env):
    client, db = env
    session = join(client, "full.flow@example.org", "Casey Morgan")
    assert session["user"]["home"] == "/medications" and session["user"]["name"] == "Casey Morgan"
    assert client.get("/api/me/profile", headers=as_user(session)).status_code == 200

    assert client.post("/api/auth/logout", headers=as_user(session)).status_code == 200
    # Signed out on the server: the old cookie is dead, not merely forgotten by the browser.
    assert client.get("/api/me/profile", headers=as_user(session)).status_code == 401

    again = login(client, "FULL.flow@example.org")
    assert again.status_code == 200 and again.json()["user"]["role"] == "patient"
    assert client.get("/api/me/profile", headers=as_user(signed_in(again))).status_code == 200
    assert login(client, "full.flow@example.org", "WrongPass12").status_code == 401
    actions = set(db.scalars(select(AuditLog.action).where(AuditLog.entity_type == "auth")))
    assert "login_failed" in actions
    assert db.scalar(select(AuditLog).where(AuditLog.action == "logout"))


def test_sign_out_ends_only_this_browser(env):
    client, _ = env
    first = signed_in(login(client, "full.flow@example.org"))
    second = signed_in(login(client, "full.flow@example.org"))
    client.post("/api/auth/logout", headers=as_user(first))
    assert client.get("/api/auth/me", headers=as_user(first)).status_code == 401
    assert client.get("/api/auth/me", headers=as_user(second)).status_code == 200


# --- Automatic assignment per role --------------------------------------------------


def test_new_patient_gets_their_own_empty_record(env):
    client, db = env
    session = join(client, "new.patient@example.org", "Riya Sharma")
    pid = session["user"]["patient_id"]
    assert pid and pid.startswith("PAT_R")  # a new record, never a synthetic one
    assert len(db.scalars(select(User).where(User.patient_id == pid)).all()) == 1

    profile = client.get("/api/me/profile", headers=as_user(session)).json()
    assert (profile["patient_id"], profile["name"]) == (pid, "Riya Sharma")
    assert profile["therapies"] == [] and profile["origin"] == "self_registered"
    for path in ("/api/patients", f"/api/patients/{pid}", "/api/patients/PAT_00002", "/api/hcps"):
        assert client.get(path, headers=as_user(session)).status_code == 403
    assert client.get("/api/me/consents", headers=as_user(session)).status_code == 200
    seen = client.get(f"/api/patients/{pid}", headers=auth(client, "admin")).json()
    assert seen["name"] == "Riya Sharma"


def test_second_patient_gets_a_different_record(env):
    client, _ = env
    first = join(client, "p.one@example.org")["user"]["patient_id"]
    second = join(client, "p.two@example.org")["user"]["patient_id"]
    assert first and second and first != second


def test_new_hcp_gets_own_record_without_commercial_fields(env):
    client, _ = env
    session = onboard(client, "new.hcp@example.org", "hcp", "Anita Rao")
    assert session["user"]["hcp_id"] and session["user"]["home"] == "/inbox"
    profile = client.get("/api/me/profile", headers=as_user(session)).json()
    assert profile["name"] == "Dr. Anita Rao"
    assert not {"segment", "value_score", "rx_volume_annual"} & set(profile)
    assert client.get("/api/me/patients", headers=as_user(session)).status_code == 200
    assert client.get("/api/hcps", headers=as_user(session)).status_code == 403


def test_new_care_manager_gets_a_panel_and_sees_only_it(env):
    client, db = env
    session = onboard(client, "new.cm@example.org", "care_manager")
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
    listed = client.get("/api/patients", params={"limit": 200}, headers=as_user(session)).json()
    assert {p["patient_id"] for p in listed["items"]} == panel
    outside = db.scalar(select(Patient.patient_id).where(Patient.patient_id.not_in(panel)))
    assert client.get(f"/api/patients/{outside}", headers=as_user(session)).status_code == 404
    assert client.get("/api/hcps", headers=as_user(session)).status_code == 403
    queue = client.get("/api/nba", params={"limit": 200}, headers=as_user(session)).json()
    assert queue["items"] and all(
        i["target_type"] == "PATIENT" and i["target_id"] in panel for i in queue["items"]
    )


def test_seeded_assignments_are_untouched_by_new_accounts(env):
    client, db = env
    cm01 = db.scalar(select(User).where(User.username == "cm01"))
    count = (
        select(func.count())
        .select_from(CareManagerPatient)
        .where(CareManagerPatient.care_manager_user_id == cm01.id)
    )
    before = db.scalar(count)
    onboard(client, "another.cm@example.org", "care_manager")
    assert before == db.scalar(count) > 0


def test_new_rep_gets_hcps_and_no_patients(env):
    client, db = env
    session = onboard(client, "new.rep@example.org", "medical_rep")
    user = db.scalar(select(User).where(User.email == "new.rep@example.org"))
    mine = set(db.scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == user.id)))
    assert len(mine) == get_settings().signup_panel_hcps
    listed = client.get("/api/hcps", params={"limit": 200}, headers=as_user(session)).json()
    assert {h["hcp_id"] for h in listed["items"]} == mine
    assert client.get("/api/patients", headers=as_user(session)).status_code == 403
    assert client.get("/api/patients/PAT_00001", headers=as_user(session)).status_code == 403
    content = client.get("/api/content", headers=as_user(session)).json()
    assert content and all(c["usable"] and c["audience"] == "HCP" for c in content)


def test_new_compliance_account_is_limited_to_compliance_functions(env):
    client, _ = env
    session = onboard(client, "new.mlr@example.org", "compliance")
    assert session["user"]["home"] == "/content"
    allowed = ("/api/content", "/api/audit", "/api/nba", "/api/analytics/overview")
    denied = ("/api/patients", "/api/hcps", "/api/admin/users", "/api/admin/config")
    assert all(client.get(p, headers=as_user(session)).status_code == 200 for p in allowed)
    assert all(client.get(p, headers=as_user(session)).status_code == 403 for p in denied)
    items = client.get("/api/nba", headers=as_user(session)).json()["items"]
    assert all(i["target_name"] is None and i["target_id"] is None for i in items)
    # Audit detail is masked for compliance: no account emails, no patient / HCP ids.
    log = client.get("/api/audit", params={"limit": 500}, headers=as_user(session)).json()
    text = str(log["items"])
    assert "@example.org" not in text and "PAT_0" not in text


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
        "/api/admin/users", params={"source": "invitation"}, headers=auth(client, "admin")
    ).json()
    emails = {u["email"] for u in listing["items"]}
    assert {"new.cm@example.org", "new.rep@example.org"} <= emails
    assert all(u["professionally_verified"] for u in listing["items"])
    assert all(
        "password" not in key and "date_of_birth" != key for u in listing["items"] for key in u
    )


def test_changing_assignments_changes_scope_but_never_role_or_verification(env):
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
    # Patient lists are ordered by latest activity, so compare membership.
    assert sorted(p["patient_id"] for p in after["patients"]) == new_panel
    assert (after["role"], after["permissions"]) == (before["role"], before["permissions"])
    assert after["permissions"] == sorted(ROLE_PERMISSIONS["care_manager"])
    assert after["professionally_verified"] and after["lineage"] == before["lineage"]

    session = signed_in(login(client, "new.cm@example.org"))
    listed = client.get("/api/patients", headers=as_user(session)).json()
    assert sorted(p["patient_id"] for p in listed["items"]) == new_panel
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
        ("new.cm@example.org", {"patient_ids": [], "role": "admin"}, 422, "invalid_request"),
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
    session = join(client, "unlinked@example.org")
    uid = account_id(db, "unlinked@example.org")
    cleared = client.put(
        f"/api/admin/users/{uid}/assignments", json={"patient_id": None}, headers=admin
    )
    assert cleared.status_code == 200 and cleared.json()["assignment"]["count"] == 0
    for path in ("/api/me/profile", "/api/me/consents", "/api/me/inbox"):
        response = client.get(path, headers=as_user(session))
        assert response.status_code == 409 and code_of(response) == "no_assignment"


def test_disable_signs_the_account_out_and_blocks_sign_in(env):
    client, db = env
    admin = auth(client, "admin")
    session = onboard(client, "to.disable@example.org", "medical_rep")
    uid = account_id(db, "to.disable@example.org")
    off = client.patch(f"/api/admin/users/{uid}/status", json={"status": "disabled"}, headers=admin)
    assert off.status_code == 200 and off.json()["status"] == "disabled"
    assert off.json()["role"] == "medical_rep"
    assert client.get("/api/nba", headers=as_user(session)).status_code == 401
    refused = login(client, "to.disable@example.org")
    # The same answer as a wrong password: sign-in never reveals that an account exists
    # or is disabled.
    wrong = login(client, "to.disable@example.org", "Not-the-password-1")
    assert refused.status_code == 401 and code_of(refused) == "invalid_credentials"
    assert refused.json() == wrong.json()

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
    stale = sign_in(client, "admin")
    reset = client.post(
        "/api/admin/reset", json={"patients": 80, "hcps": 16}, headers=auth(client, "admin")
    )
    assert reset.status_code == 200, reset.text
    client.__dict__["_tokens"].clear()
    assert client.get("/api/auth/me", headers=cookie_header(nba_session=stale)).status_code == 401

    back = login(client, "new.cm@example.org")
    assert back.status_code == 200
    listed = client.get("/api/patients", headers=as_user(signed_in(back))).json()
    ids = [p["patient_id"] for p in listed["items"]]
    assert sorted(i for i in ids if not i.startswith("PAT_R")) == [
        "PAT_00001",
        "PAT_00002",
        "PAT_00003",
    ]
    # Real patients the care manager looks after are not demo data: they survive the reset.
    real = [p for p in listed["items"] if p["origin"] != "synthetic"]
    assert all(p["patient_id"].startswith("PAT_R") for p in real)
    hcp = signed_in(login(client, "new.hcp@example.org"))
    assert client.get("/api/me/profile", headers=as_user(hcp)).json()["name"] == "Dr. Anita Rao"
    assert hcp["user"]["professionally_verified"]
    # Seeded demo accounts and the fixed administrator are rebuilt as before.
    assert sign_in(client, "cm01") and sign_in(client, "admin")
    assert login(client, "pending.person@example.org").status_code == 403
    # Security audit and invitations survived; lineage still points at the administrator.
    assert db.scalar(select(AuditLog).where(AuditLog.action == "invitation_accepted"))
    uid = account_id(db, "new.cm@example.org")
    detail = client.get(f"/api/admin/users/{uid}", headers=auth(client, "admin")).json()
    assert [p["role"] for p in detail["lineage"]] == ["admin", "care_manager"]
