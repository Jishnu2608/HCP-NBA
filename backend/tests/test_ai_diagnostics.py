"""Admin context-layer diagnostics over HTTP: administrator only on every endpoint, strict
bodies, redacted record previews, sanitized reports, audited runs with codes only, rate limits,
CSRF, and no write to any business record."""

import json

import pytest
from conftest import ApiClient, auth, new_session
from context_world import table_hashes
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import cycle
from app.ai import diagnostics, fixtures
from app.core import ratelimit
from app.core.config import get_settings
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import AuditLog, CareManagerPatient, User

# Operations the owner approved (Case Brief 2026-10-16; five more 2026-10-19).
APPROVED = (
    "case_brief",
    "compliance_summary",
    "cycle_time",
    "decision_trace",
    "engagement_brief",
    "queue_brief",
)

GETS = ["/api/ai/diagnostics/sources", "/api/ai/diagnostics/policy", "/api/ai/diagnostics/health"]
POSTS = [
    ("/api/ai/diagnostics/context", {"subject_type": "patient"}),
    ("/api/ai/diagnostics/safety", {}),
    ("/api/ai/diagnostics/aggregate", {}),
    ("/api/ai/diagnostics/report", {}),
    ("/api/ai/diagnostics/provider-check", {}),
]


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=41, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=2))
    cycle.run(db)
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


@pytest.fixture(autouse=True)
def _offline_provider(monkeypatch):
    # The provider check must not depend on a local Ollama during tests: point it at a closed
    # loopback port (still the real code path, still loopback only).
    monkeypatch.setattr(get_settings(), "ai_ollama_base_url", "http://127.0.0.1:9")


@pytest.mark.parametrize("who", ["cm01", "rep01", "hcp0001", "compliance1"])
def test_every_endpoint_refuses_every_other_role(env, who):
    client, _ = env
    headers = auth(client, who)
    for path in GETS:
        assert client.get(path, headers=headers).status_code == 403, path
    for path, body in POSTS:
        assert client.post(path, json=body, headers=headers).status_code == 403, path


def test_signed_out_requests_are_refused(env):
    client, _ = env
    assert client.get(GETS[0]).status_code == 401
    assert client.post(POSTS[1][0], json={}).status_code in (401, 403)


def test_the_administrator_sees_sources_policy_and_health(env):
    client, _ = env
    headers = auth(client, "admin")
    sources = client.get(GETS[0], headers=headers).json()
    ids = {s["id"]: s for s in sources["sources"]}
    assert ids["patient_360"]["status"] == "implemented"
    assert ids["patient_self"]["status"] == "policy_blocked"
    assert ids["inbox"]["status"] == "unsupported" and ids["inbox"]["status_reason"]
    assert tuple(o["id"] for o in sources["operations"]) == APPROVED
    policy = client.get(GETS[1], headers=headers).json()
    assert policy["records"]["care_request"]["reason"] == "reader_only"
    assert "care_request.reason" not in policy["model_visible"]
    health = client.get(GETS[2], headers=headers).json()
    assert health["provider"]["state"] in ("configured_unprobed", "disabled", "misconfigured")
    assert health["provider"]["probed"] is False
    assert all(p["state"] in ("ok", "partial") for p in health["read_service_probes"]), health[
        "read_service_probes"
    ]


def test_fixture_mode_explains_role_decisions_without_record_content(env):
    client, _ = env
    headers = auth(client, "admin")
    ok = client.post(
        POSTS[0][0], json={"subject_type": "patient", "role": "care_manager"}, headers=headers
    ).json()
    assert {c["check"]: c["result"] for c in ok["checks"]}["free_text_isolation"] == "pass"
    assert ok["context"]["redacted"] is False and ok["context"]["field_decisions"]
    denied = client.post(
        POSTS[0][0], json={"subject_type": "patient", "role": "medical_rep"}, headers=headers
    ).json()
    assert denied["context"] is None and denied["checks"][0]["code"] == "forbidden"
    scoped = client.post(
        POSTS[0][0],
        json={"subject_type": "patient", "role": "care_manager", "subject": "out_of_scope"},
        headers=headers,
    ).json()
    assert scoped["checks"][0]["code"] == "not_found_or_out_of_scope"
    for c in fixtures.CANARIES:
        assert c not in json.dumps(ok) + json.dumps(denied) + json.dumps(scoped)


def test_record_preview_reads_with_the_admins_own_access_and_redacts_every_value(env):
    client, db = env
    pid = db.scalar(select(CareManagerPatient.patient_id).order_by(CareManagerPatient.patient_id))
    got = client.post(
        POSTS[0][0],
        json={"mode": "record_preview", "subject_type": "patient", "subject": pid},
        headers=auth(client, "admin"),
    )
    assert got.status_code == 200, got.text
    body = got.json()
    ctx = body["context"]
    assert ctx["redacted"] and ctx["differs_from_model_payload"]
    assert {i["text"] for i in ctx["items"]} == {"[redacted]"}
    assert pid not in got.text  # not even the id it was asked about
    assert "administrator" in body["principal"]
    # A self subject is the admin's own: there is none, so nothing is read for anyone else.
    selfish = client.post(
        POSTS[0][0],
        json={"mode": "record_preview", "subject_type": "patient_self"},
        headers=auth(client, "admin"),
    ).json()
    assert selfish["context"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"subject_type": "patient", "extra": 1},
        {"subject_type": "salary"},
        {"subject_type": "patient", "mode": "raw"},
        {"subject_type": "patient", "role": "superuser"},
        {"subject_type": "patient", "subject": "PAT 1; drop"},
        {"subject_type": "patient", "categories": ["x"] * 30},
    ],
)
def test_malformed_diagnostic_requests_are_refused(env, body):
    client, _ = env
    got = client.post(POSTS[0][0], json=body, headers=auth(client, "admin"))
    if got.status_code == 200:  # a valid shape refused by the checks themselves
        assert got.json()["context"] is None
    else:
        assert got.status_code == 422


def test_safety_aggregate_and_report_run_on_fixtures_and_are_sanitized(env):
    client, _ = env
    headers = auth(client, "admin")
    safety = client.post(
        POSTS[1][0], json={"text": "No refill record is on file."}, headers=headers
    ).json()
    assert safety["failed"] == 0 and safety["passed"] >= 14
    assert any(c["id"] == "custom_text" for c in safety["cases"])
    agg = client.post(POSTS[2][0], json={}, headers=headers).json()
    assert agg["multi_subject"]["out_of_scope"] == 2 and not agg["multi_subject"]["complete"]
    report = client.post(POSTS[3][0], json={}, headers=headers)
    assert report.status_code == 200
    data = report.json()
    assert data["model_calls"] == 0 and data["failures"] == []
    text = report.text
    assert "@" not in text and "PAT_" not in text and "HCP_" not in text
    for c in fixtures.CANARIES:
        assert c not in text


def test_the_report_sanitizer_refuses_unsafe_content():
    for bad in (
        {"x": fixtures.CANARY_NOTE},
        {"x": "a@b.example"},
        {"x": "PAT_00012"},
        {"x": "Jane Smithson"},
    ):
        with pytest.raises(diagnostics.UnsafeReport):
            diagnostics.sanitize_report(bad, ["Smithson"])


def test_runs_are_audited_with_codes_only(env):
    client, db = env
    client.post(
        POSTS[0][0],
        json={"subject_type": "patient", "role": "care_manager"},
        headers=auth(client, "admin"),
    )
    rows = db.scalars(select(AuditLog).where(AuditLog.action == "ai_diagnostics_run")).all()
    assert rows
    blob = json.dumps([[r.entity_id, r.detail, r.reason] for r in rows])
    assert "PAT_" not in blob and "HCP_" not in blob
    assert {r.entity_id for r in rows} >= {"context"}


def test_no_business_record_changes(env):
    client, db = env
    before = table_hashes(db)
    headers = auth(client, "admin")
    for path in GETS:
        client.get(path, headers=headers)
    for path, body in POSTS:
        client.post(path, json=body, headers=headers)
    db.expire_all()
    after = table_hashes(db)
    # Signing in touches the session and account tables; nothing else may change.
    changed = {t for t in before if before[t] != after.get(t)}
    assert changed <= {"user_session", "user", "rate_limit_hit"}, changed


def test_expensive_runs_are_rate_limited(env, monkeypatch):
    client, db = env
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    monkeypatch.setitem(ratelimit.LIMITS, "ai_diagnostics", (2, 600))
    admin = db.scalar(select(User).where(User.role == "admin"))
    ratelimit.clear(db, "ai_diagnostics", str(admin.id))
    db.commit()
    headers = auth(client, "admin")
    codes = [client.post(POSTS[1][0], json={}, headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_state_changing_runs_need_the_csrf_token(env):
    client, _ = env
    raw = TestClient(app)
    got = raw.post(POSTS[1][0], json={}, headers=auth(client, "admin"))
    assert got.status_code == 403


# --- F-09: diagnostics reads are audited and rate limited --------------------------------------


def test_diagnostics_reads_are_audited_and_limited(env, monkeypatch):
    client, db = env
    headers = auth(client, "admin")
    before = db.query(AuditLog).filter_by(action="ai_diagnostics_view").count()
    for path in GETS:
        assert client.get(path, headers=headers).status_code == 200
    db.expire_all()
    rows = db.query(AuditLog).filter_by(action="ai_diagnostics_view").all()
    assert len(rows) == before + len(GETS)
    assert {r.entity_id for r in rows[-len(GETS) :]} == {"sources", "policy", "health"}
    assert all(r.detail is None for r in rows)  # who and which section; no values
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    monkeypatch.setitem(ratelimit.LIMITS, "ai_diagnostics_read", (2, 600))
    admin = db.scalar(select(User).where(User.role == "admin"))
    ratelimit.clear(db, "ai_diagnostics_read", str(admin.id))
    db.commit()
    codes = [client.get(GETS[2], headers=headers).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


# --- AI context evaluation endpoints ------------------------------------------------------------

EVAL_GET = "/api/ai/diagnostics/evaluation"
EVAL_RUN = "/api/ai/diagnostics/evaluation/run"


@pytest.mark.parametrize("who", ["cm01", "rep01", "hcp0001", "compliance1"])
def test_evaluation_endpoints_refuse_every_other_role(env, who):
    client, _ = env
    headers = auth(client, who)
    assert client.get(EVAL_GET, headers=headers).status_code == 403
    assert client.post(EVAL_RUN, headers=headers).status_code == 403


def test_evaluation_needs_a_session_and_the_csrf_token(env):
    client, _ = env
    assert client.get(EVAL_GET).status_code == 401
    raw = TestClient(app)
    assert raw.post(EVAL_RUN, headers=auth(client, "admin")).status_code == 403


def test_evaluation_run_records_codes_only_and_changes_no_business_record(env):
    client, db = env
    headers = auth(client, "admin")
    before = table_hashes(db)
    got = client.post(EVAL_RUN, headers=headers)
    assert got.status_code == 200, got.text
    body = got.json()
    result = body["result"]
    assert result["counts"]["failed"] == 0
    assert result["score"]["readiness"] in ("below_threshold", "eligible_for_owner_review")
    assert tuple(result["registered_operations"]) == APPROVED
    assert "payload_sent" not in json.dumps(body["export"])
    assert not any(c in got.text for c in fixtures.CANARIES[:3])
    last = client.get(EVAL_GET, headers=headers).json()
    assert last["readiness"] == result["score"]["readiness"]
    assert last["last"]["confidence"] == result["score"]["confidence"]
    row = db.scalars(select(AuditLog).where(AuditLog.action == "ai_evaluation_run")
                     .order_by(AuditLog.id.desc())).first()  # fmt: skip
    assert set(row.detail) == {"rubric", "confidence", "readiness", "revision", "counts",
                               "pass_rate", "digest", "results"}  # fmt: skip
    assert set(row.detail["results"].values()) <= {"pass", "fail", "not_applicable"}
    db.expire_all()
    after = table_hashes(db)
    changed = {t for t in before if before[t] != after.get(t)}
    assert changed <= {"user_session", "user", "rate_limit_hit"}, changed


def test_evaluation_runs_are_rate_limited(env, monkeypatch):
    client, db = env
    monkeypatch.setattr(get_settings(), "rate_limit_enabled", True)
    monkeypatch.setitem(ratelimit.LIMITS, "ai_evaluation", (1, 600))
    admin = db.scalar(select(User).where(User.role == "admin"))
    ratelimit.clear(db, "ai_evaluation", str(admin.id))
    db.commit()
    headers = auth(client, "admin")
    codes = [client.post(EVAL_RUN, headers=headers).status_code for _ in range(2)]
    assert codes == [200, 429]
