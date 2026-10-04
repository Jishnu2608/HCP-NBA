"""API behaviour, with the emphasis on who is allowed to see and do what."""

import pytest
from conftest import DEMO_PASSWORD, ApiClient, cookie_header, login, new_session, sign_in
from sqlalchemy import select

from app import cycle
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import AuditLog, CareManagerPatient, Content, Nba, RepHcp, User
from app.models.enums import NbaStatus

PERSONAS = {
    "admin": "admin",
    "compliance": "compliance1",
    "rep": "rep01",
    "other_rep": "rep02",
    "cm": "cm01",
    "other_cm": "cm02",
    "hcp": "hcp0002",
    "patient": "pat00001",
}


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=21, n_patients=200, n_hcps=40, n_reps=4, n_care_managers=3))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    client = ApiClient(app)
    tokens = {role: sign_in(client, name) for role, name in PERSONAS.items()}
    yield client, db, tokens
    app.dependency_overrides.clear()
    db.close()


def call(env, role, method, path, **kw):
    client, _, tokens = env
    return client.request(method, path, headers=cookie_header(nba_session=tokens[role]), **kw)


def open_nba(db, target_type, target_id=None, status=NbaStatus.READY_FOR_REVIEW) -> Nba:
    where = [Nba.target_type == target_type, Nba.status == status]
    if target_id:
        where.append(Nba.target_id == target_id)
    return db.scalar(select(Nba).where(*where).order_by(Nba.priority.desc()))


# --- Authentication -------------------------------------------------------------


def test_login_with_password_and_reject_bad_credentials(env):
    client, *_ = env
    ok = login(client, "cm01@nba.demo", DEMO_PASSWORD)
    assert ok.status_code == 200
    assert ok.json()["user"]["role"] == "care_manager" and ok.json()["user"]["home"] == "/queue"
    assert "access_token" not in ok.json()  # the session is only in the HttpOnly cookie
    bad = login(client, "cm01@nba.demo", "wrong")
    unknown = login(client, "nobody@nba.demo", "x")
    # Same answer for a wrong password and an unknown email: nothing reveals which it was.
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()
    assert bad.json()["detail"]["code"] == "invalid_credentials"


def test_requests_without_a_valid_token_are_refused(env):
    client, *_ = env
    assert client.get("/api/nba").status_code == 401
    assert client.get("/api/nba", headers=cookie_header(nba_session="nonsense")).status_code == 401
    forged = env[2]["admin"][:-3] + "abc"
    assert client.get("/api/nba", headers=cookie_header(nba_session=forged)).status_code == 401
    # A bearer header is not a way in either.
    token = env[2]["admin"]
    assert client.get("/api/nba", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_persona_picker_endpoints_are_gone(env):
    client, *_ = env
    assert client.get("/api/auth/personas").status_code in (404, 405)
    assert client.post("/api/auth/demo-login", json={"username": "admin"}).status_code in (404, 405)


# --- Role by endpoint matrix -----------------------------------------------------

ALLOWED = {
    ("GET", "/api/nba"): {"admin", "compliance", "rep", "cm"},
    ("GET", "/api/admin/users"): {"admin"},
    ("GET", "/api/analytics/overview"): {"admin", "compliance"},
    ("GET", "/api/clock"): {"admin", "compliance", "rep", "cm", "hcp", "patient"},
    ("GET", "/api/patients"): {"admin", "cm"},
    ("GET", "/api/patients/PAT_00001"): {"admin", "cm"},
    ("GET", "/api/hcps"): {"admin", "rep"},
    ("GET", "/api/hcps/HCP_0001"): {"admin", "rep"},
    ("GET", "/api/content"): {"admin", "compliance", "rep", "cm"},
    ("GET", "/api/audit"): {"admin", "compliance"},
    ("GET", "/api/admin/config"): {"admin"},
    ("GET", "/api/admin/cycles"): {"admin"},
    ("GET", "/api/admin/models"): {"admin", "compliance"},
    ("GET", "/api/me/profile"): {"hcp", "patient"},
    ("GET", "/api/me/consents"): {"patient"},
    ("GET", "/api/me/patients"): {"hcp"},
}
ROLES = ["admin", "compliance", "rep", "cm", "hcp", "patient"]


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(("method", "path"), list(ALLOWED))
def test_endpoint_access_by_role(env, role, method, path):
    response = call(env, role, method, path)
    if role in ALLOWED[(method, path)]:
        assert response.status_code == 200, response.text
    else:
        assert response.status_code == 403


# --- Row-level scoping -----------------------------------------------------------


def test_queues_only_contain_assigned_targets(env):
    _, db, _ = env
    rep = db.scalar(select(User).where(User.username == "rep01"))
    mine = set(db.scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == rep.id)))
    items = call(env, "rep", "GET", "/api/nba", params={"limit": 200}).json()["items"]
    assert items and all(i["target_type"] == "HCP" and i["target_id"] in mine for i in items)

    cm = db.scalar(select(User).where(User.username == "cm01"))
    managed = set(
        db.scalars(
            select(CareManagerPatient.patient_id).where(
                CareManagerPatient.care_manager_user_id == cm.id
            )
        )
    )
    items = call(env, "cm", "GET", "/api/nba", params={"limit": 200}).json()["items"]
    assert items and all(i["target_type"] == "PATIENT" and i["target_id"] in managed for i in items)
    listed = call(env, "cm", "GET", "/api/patients", params={"limit": 200}).json()
    assert {p["patient_id"] for p in listed["items"]} == managed


def test_out_of_scope_records_look_missing(env):
    _, db, _ = env
    assert call(env, "other_rep", "GET", "/api/hcps/HCP_0001").status_code == 404
    assert call(env, "other_cm", "GET", "/api/patients/PAT_00001").status_code == 404
    hcp_nba = open_nba(db, "HCP", "HCP_0001")
    patient_nba = open_nba(db, "PATIENT", "PAT_00001")
    assert call(env, "rep", "GET", f"/api/nba/{hcp_nba.id}").status_code == 200
    assert call(env, "other_rep", "GET", f"/api/nba/{hcp_nba.id}").status_code == 404
    assert call(env, "cm", "GET", f"/api/nba/{hcp_nba.id}").status_code == 404
    assert call(env, "rep", "GET", f"/api/nba/{patient_nba.id}").status_code == 404
    assert (
        call(env, "other_cm", "POST", f"/api/nba/{patient_nba.id}/approve", json={}).status_code
        == 404
    )
    for role in ("hcp", "patient"):
        assert call(env, role, "GET", "/api/nba").status_code == 403
        assert call(env, role, "GET", f"/api/nba/{patient_nba.id}").status_code == 403


def test_compliance_sees_gate_outcomes_but_not_identities_and_cannot_review(env):
    _, db, _ = env
    items = call(env, "compliance", "GET", "/api/nba", params={"limit": 200}).json()["items"]
    assert items
    assert all(i["status"] == "blocked" or i["has_withheld"] for i in items)
    assert all(i["target_name"] is None and i["segment"] is None for i in items)
    assert all(i["target_id"] is None for i in items)
    withheld = next(i for i in items if i["has_withheld"] and i["status"] == "ready_for_review")
    # The detail view leaves out the drafts (they address the person by name).
    detail = call(env, "compliance", "GET", f"/api/nba/{withheld['id']}").json()
    assert detail["target_id"] is None and detail["drafts"] == []
    assert (
        call(env, "compliance", "POST", f"/api/nba/{withheld['id']}/approve", json={}).status_code
        == 403
    )


def test_field_roles_only_see_usable_content_for_their_audience(env):
    rep_content = call(env, "rep", "GET", "/api/content").json()
    assert rep_content and all(c["usable"] and c["audience"] == "HCP" for c in rep_content)
    cm_content = call(env, "cm", "GET", "/api/content").json()
    assert cm_content and all(c["usable"] and c["audience"] == "PATIENT" for c in cm_content)
    everything = call(env, "compliance", "GET", "/api/content").json()
    assert {c["mlr_status"] for c in everything} == {"approved", "pending", "rejected"}
    assert (
        call(
            env, "rep", "POST", "/api/content/CNT_001/review", json={"decision": "approve"}
        ).status_code
        == 403
    )
    assert (
        call(
            env, "admin", "POST", "/api/content/CNT_001/review", json={"decision": "approve"}
        ).status_code
        == 403
    )


def test_portal_users_see_only_themselves(env):
    patient = call(env, "patient", "GET", "/api/me/profile").json()
    assert patient["patient_id"] == "PAT_00001"
    assert "risk_segment" not in patient and "risk_score" not in patient["therapies"][0]
    hcp = call(env, "hcp", "GET", "/api/me/profile").json()
    assert hcp["hcp_id"] == "HCP_0002"
    assert not {"segment", "value_score", "rx_volume_annual"} & set(hcp)


def test_hcp_sees_patient_adherence_only_with_sharing_consent(env):
    client, _, tokens = env
    shared = call(env, "hcp", "GET", "/api/me/patients").json()
    ids = {p["patient_id"] for p in shared}
    assert "PAT_00001" in ids  # prescribed by HCP_0002, sharing granted
    assert all("risk_score" not in t for p in shared for t in p["therapies"])

    # The patient withdraws sharing; the HCP loses sight of them immediately.
    off = call(env, "patient", "PUT", "/api/me/consents/provider-sharing", json={"granted": False})
    assert off.status_code == 200 and off.json()["granted"] is False
    assert "PAT_00001" not in {
        p["patient_id"] for p in call(env, "hcp", "GET", "/api/me/patients").json()
    }
    call(env, "patient", "PUT", "/api/me/consents/provider-sharing", json={"granted": True})
    assert "PAT_00001" in {
        p["patient_id"] for p in call(env, "hcp", "GET", "/api/me/patients").json()
    }

    # PAT_00002 is treated by HCP_0001 but never consented to sharing.
    cardiologist = sign_in(client, "hcp0001")
    seen = client.get("/api/me/patients", headers=cookie_header(nba_session=cardiologist)).json()
    assert "PAT_00002" not in {p["patient_id"] for p in seen}


# --- Review workflow ---------------------------------------------------------------


def test_profile_pages_return_the_360_view(env):
    p = call(env, "cm", "GET", "/api/patients/PAT_00001").json()
    assert p["risk_segment"] == "high" and p["therapies"][0]["gap_days"] == 16
    assert len(p["fills"]) == 7 and p["open_nba"]["action"] and p["features"]["channels"]["sms"]
    h = call(env, "rep", "GET", "/api/hcps/HCP_0001").json()
    assert h["segment"] and h["features"]["channels"]["email"]["sent"] == 6 and h["interactions"]


def test_detail_shows_rationale_alternatives_drafts_and_audit(env):
    _, db, _ = env
    nba = open_nba(db, "PATIENT", "PAT_00001")
    d = call(env, "cm", "GET", f"/api/nba/{nba.id}").json()
    assert d["can_review"] and d["rationale"] and d["content"]["usable"]
    assert {r["kind"] for r in d["reason_codes"]} >= {
        "who",
        "action",
        "channel",
        "timing",
        "compliance",
    }
    assert sum(c["chosen"] for c in d["candidates"]) == 1 and len(d["candidates"]) > 3
    assert len(d["drafts"]) == 2 and sum(x["is_selected"] for x in d["drafts"]) == 1
    assert [a["action"] for a in d["audit"]] == ["nba_generated"]


def test_edit_draft_is_validated_then_approve_and_audit(env):
    _, db, _ = env
    nba = open_nba(db, "PATIENT", "PAT_00001")
    draft = call(env, "cm", "GET", f"/api/nba/{nba.id}").json()["drafts"][1]
    url = f"/api/nba/{nba.id}/drafts/{draft['id']}"
    bad = call(env, "cm", "PATCH", url, json={"body": "This will cure you, see https://x.example"})
    assert bad.status_code == 422 and len(bad.json()["detail"]) == 2
    good = call(env, "cm", "PATCH", url, json={"body": "Hi Margaret, your refill is ready."})
    assert good.status_code == 200 and good.json()["edited"] and good.json()["is_selected"]

    approved = call(env, "cm", "POST", f"/api/nba/{nba.id}/approve", json={})
    assert approved.status_code == 200 and approved.json()["status"] == "approved"
    assert [a["action"] for a in approved.json()["audit"]] == [
        "nba_generated",
        "draft_edited",
        "nba_approved",
    ]
    assert approved.json()["audit"][-1]["actor"] == "cm01"
    assert call(env, "cm", "POST", f"/api/nba/{nba.id}/approve", json={}).status_code == 409


def test_reject_needs_a_reason(env):
    _, db, _ = env
    nba = open_nba(db, "HCP", "HCP_0003") or open_nba(db, "HCP")
    owner = "rep" if nba.target_id in ("HCP_0001", "HCP_0002", "HCP_0003") else "admin"
    assert (
        call(env, owner, "POST", f"/api/nba/{nba.id}/reject", json={"reason": ""}).status_code
        == 422
    )
    done = call(
        env, owner, "POST", f"/api/nba/{nba.id}/reject", json={"reason": "Visited last week"}
    )
    assert (
        done.json()["status"] == "rejected"
        and done.json()["audit"][-1]["reason"] == "Visited last week"
    )


def test_approval_rechecks_consent_withdrawn_after_generation(env):
    """The patient opts out between generation and review: approval must fail and block."""
    _, db, _ = env
    nba = open_nba(db, "PATIENT", "PAT_00005")
    assert nba is not None
    client, _, tokens = env
    rosa = sign_in(client, "pat00005")
    off = client.put(
        f"/api/me/consents/outreach/{nba.channel}",
        json={"granted": False},
        headers=cookie_header(nba_session=rosa),
    )
    assert off.status_code == 200
    response = call(env, "cm", "POST", f"/api/nba/{nba.id}/approve", json={})
    assert response.status_code == 409 and "not consented" in response.json()["detail"]
    db.refresh(nba)
    assert nba.status == NbaStatus.BLOCKED
    log = db.scalar(
        select(AuditLog).where(
            AuditLog.nba_id == nba.id, AuditLog.action == "nba_blocked_at_review"
        )
    )
    assert log.consent_ok is False and log.actor == "cm01"
    withdrawn = db.scalar(
        select(AuditLog).where(AuditLog.action == "consent_withdrawn", AuditLog.actor == "pat00005")
    )
    assert withdrawn.detail["channel"] == nba.channel


def test_mlr_approval_by_compliance_then_cycle_uses_the_content(env):
    _, db, _ = env
    pending = db.scalar(
        select(Content).where(Content.topic == "diabetes_outcomes", Content.mlr_status == "pending")
    )
    before = next(
        c
        for c in call(env, "compliance", "GET", "/api/content").json()
        if c["content_id"] == pending.content_id
    )
    assert not before["usable"]
    done = call(
        env, "compliance", "POST", f"/api/content/{pending.content_id}/review",
        json={"decision": "approve", "comment": "Claims verified"},
    )  # fmt: skip
    assert done.status_code == 200 and done.json()["usable"]
    assert call(env, "admin", "POST", "/api/admin/cycle").status_code == 200
    rep_content = {c["content_id"] for c in call(env, "rep", "GET", "/api/content").json()}
    assert pending.content_id in rep_content
    actions = call(
        env, "compliance", "GET", "/api/audit", params={"action": "content_approved"}
    ).json()
    assert actions["items"][0]["actor"] == "compliance1"
    rejected = db.scalar(select(Content).where(Content.mlr_status == "rejected"))
    again = call(
        env, "compliance", "POST", f"/api/content/{rejected.content_id}/review",
        json={"decision": "approve"},
    )  # fmt: skip
    assert again.status_code == 409


def test_admin_config_is_typed_and_audited(env):
    assert call(env, "admin", "PUT", "/api/admin/config/nope", json={"value": 1}).status_code == 404
    assert (
        call(
            env, "admin", "PUT", "/api/admin/config/pdc_threshold", json={"value": "x"}
        ).status_code
        == 422
    )
    ok = call(env, "admin", "PUT", "/api/admin/config/pdc_threshold", json={"value": 0.85})
    assert ok.status_code == 200
    assert call(env, "admin", "GET", "/api/admin/config").json()["pdc_threshold"] == {
        "value": 0.85,
        "default": 0.8,
    }
    call(env, "admin", "PUT", "/api/admin/config/pdc_threshold", json={"value": 0.8})
    changes = call(env, "admin", "GET", "/api/audit", params={"action": "config_changed"}).json()
    assert changes["total"] == 2 and changes["items"][-1]["detail"] == {
        "previous": 0.8,
        "new": 0.85,
    }
