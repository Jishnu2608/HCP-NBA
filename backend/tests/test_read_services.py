"""Read services (app/services): typed, scoped, HTTP-free, and the API answers through them."""

import ast
import json
from pathlib import Path

import pytest
from conftest import ApiClient, auth, new_session
from pydantic import ValidationError
from sqlalchemy import select

from app import cycle
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.engagement import delivery
from app.main import app
from app.models import CareManagerPatient, Interaction, Nba, Patient, RepHcp, User
from app.models.enums import NbaStatus, TargetType
from app.services import content, hcps, inbox, patients, recommendations
from app.services.errors import Forbidden, NoRecord, NotFound
from app.services.schemas import (
    ConsentOut,
    HcpProfile,
    InboxItem,
    PatientProfile,
    RecommendationDetail,
)

APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(scope="module")
def env():
    db = new_session()
    generate(db, GenConfig(seed=19, n_patients=140, n_hcps=28, n_reps=3, n_care_managers=2))
    cycle.run(db)
    admin = db.scalar(select(User).where(User.username == "admin"))
    # Deliver one written message to a seeded patient and a seeded HCP, for the inbox.
    for target in ("PAT_00001", "HCP_0001"):
        nba = db.scalar(
            select(Nba).where(
                Nba.target_id == target,
                Nba.status == NbaStatus.READY_FOR_REVIEW,
                Nba.channel.in_(("email", "portal", "sms")),
            )
        )
        if nba is not None:
            nba.status, nba.reviewed_by_user_id = NbaStatus.APPROVED, admin.id
            try:
                delivery.send(db, nba, admin)
            except (delivery.SendBlocked, delivery.NotDue):
                pass
    db.commit()
    app.dependency_overrides[get_db] = lambda: db
    yield ApiClient(app), db
    app.dependency_overrides.clear()
    db.close()


def user(db, username):
    return db.scalar(select(User).where(User.username.in_((username, f"{username}@nba.demo"))))


def own_patient(db, cm):
    return db.scalar(
        select(CareManagerPatient.patient_id).where(
            CareManagerPatient.care_manager_user_id == cm.id
        )
    )


def other_patient(db, cm):
    mine = select(CareManagerPatient.patient_id).where(
        CareManagerPatient.care_manager_user_id == cm.id
    )
    return db.scalar(select(Patient.patient_id).where(Patient.patient_id.not_in(mine)))


# --- Typed results without HTTP ------------------------------------------------------------


def test_patient_profile_is_typed_and_scoped(env):
    _, db = env
    cm = user(db, "cm01")
    profile = patients.patient_profile(db, cm, own_patient(db, cm))
    assert isinstance(profile, PatientProfile)
    assert all(isinstance(c, ConsentOut) for c in profile.consents)
    assert profile.therapies and profile.therapies[0].persistence is not None
    with pytest.raises(NotFound):
        patients.patient_profile(db, cm, other_patient(db, cm))
    with pytest.raises(Forbidden):
        patients.patient_profile(db, user(db, "rep01"), own_patient(db, cm))
    with pytest.raises(Forbidden):
        patients.patient_profile(db, user(db, "pat00001"), "PAT_00001")


def test_hcp_profile_is_typed_scoped_and_carries_no_patient_ids(env):
    _, db = env
    rep = user(db, "rep01")
    mine = list(db.scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == rep.id)))
    profile = hcps.hcp_profile(db, rep, mine[0])
    assert isinstance(profile, HcpProfile)
    assert "PAT_" not in profile.panel_adherence.model_dump_json()
    for row in profile.panel_adherence.measures:
        assert row.suppressed or row.patients >= profile.panel_adherence.min_cohort
    other = db.scalar(select(RepHcp.hcp_id).where(RepHcp.hcp_id.not_in(mine)))
    if other:
        with pytest.raises(NotFound):
            hcps.hcp_profile(db, rep, other)
    with pytest.raises(Forbidden):
        hcps.hcp_profile(db, user(db, "cm01"), mine[0])


def test_recommendation_detail_hides_identity_from_gate_readers(env):
    client, db = env
    blocked = db.scalar(select(Nba.id).where(Nba.status == NbaStatus.BLOCKED))
    reviewer_view = recommendations.recommendation_detail(db, user(db, "admin"), blocked)
    assert isinstance(reviewer_view, RecommendationDetail)
    assert reviewer_view.target_id and reviewer_view.content.body_for_recipient
    # Compliance holds nba:read:gated: the same structure, identity and wording left out.
    auth(client, "compliance1")
    gated = recommendations.recommendation_detail(db, user(db, "compliance1"), blocked)
    assert gated.target_id is None and gated.target_name is None and gated.segment is None
    assert gated.drafts == [] and gated.content.body_for_recipient is None
    assert all(r.kind and r.code for r in gated.reason_codes)
    ready = db.scalar(
        select(Nba.id).where(Nba.status == NbaStatus.READY_FOR_REVIEW, Nba.withheld.is_(None))
    )
    with pytest.raises(NotFound):
        recommendations.recommendation_detail(db, user(db, "compliance1"), ready)


def test_content_version_respects_audience_and_status(env):
    _, db = env
    cm, rep = user(db, "cm01"), user(db, "rep01")
    usable = content.approved_content(db, cm)
    assert usable and {c.audience for c in usable} == {TargetType.PATIENT}
    assert all(c.usable for c in usable)
    hcp_item = content.approved_content(db, rep)[0]
    with pytest.raises(NotFound):
        content.content_version(db, cm, hcp_item.content_id)
    assert content.content_version(db, rep, hcp_item.content_id).content_id == hcp_item.content_id


def test_inbox_is_own_only_bounded_and_paged(env):
    _, db = env
    patient = user(db, "pat00001")
    items = inbox.inbox(db, patient)
    assert all(isinstance(i, InboxItem) for i in items)
    # Patient items never carry the HCP-only fields.
    for i in items:
        assert "conversation" not in i.model_dump(exclude_unset=True)
    others = db.scalar(
        select(Interaction.id).where(
            Interaction.target_id != "PAT_00001", Interaction.source == "nba"
        )
    )
    if others:
        with pytest.raises(NotFound):
            inbox.own_item(db, patient, others)
    with pytest.raises(Forbidden):
        inbox.inbox(db, user(db, "cm01"))
    if items:
        assert inbox.inbox(db, patient, before=items[-1].id) == items[len(items) :]
        assert len(inbox.inbox(db, patient, limit=1)) == min(1, len(items))


def test_self_reader_without_a_record_gets_no_record(env):
    _, db = env
    patient = user(db, "pat00001")
    detached = User(
        username="x", email="x@example.org", display_name="X", password_hash="x",
        role=patient.role, verified=True, status="active",
    )  # fmt: skip
    with pytest.raises(NoRecord):
        inbox.inbox(db, detached)


# --- Contracts -----------------------------------------------------------------------------


def test_contracts_refuse_unknown_fields_and_keep_number_types():
    with pytest.raises(ValidationError):
        ConsentOut(
            purpose="outreach", channel="sms", granted=True, effective_from="2026-01-01",
            effective_to=None, in_effect=True, source="enrollment", extra="no",
        )  # fmt: skip
    from app.services.schemas import PanelMeasure

    m = PanelMeasure(
        measure="diabetes", label="x", level="area", scope="s", suppressed=False,
        patients=12, adherent_share=1, mean_pdc=0.5,
    )  # fmt: skip
    dumped = json.loads(m.model_dump_json())
    assert dumped["adherent_share"] == 1 and isinstance(dumped["adherent_share"], int)
    assert isinstance(dumped["mean_pdc"], float)


def test_api_answers_through_the_services(env):
    client, db = env
    cm = user(db, "cm01")
    pid = own_patient(db, cm)
    api = client.get(f"/api/patients/{pid}", headers=auth(client, "cm01")).json()
    service = json.loads(patients.patient_profile(db, cm, pid).model_dump_json(exclude_unset=True))
    for volatile in ("last_cycle_at",):
        api.pop(volatile, None), service.pop(volatile, None)
    assert api == service
    assert (
        client.get(
            f"/api/patients/{other_patient(db, cm)}", headers=auth(client, "cm01")
        ).status_code
        == 404
    )
    assert client.get(f"/api/patients/{pid}", headers=auth(client, "rep01")).status_code == 403


def test_openapi_documents_the_typed_contracts(env):
    client, _ = env
    spec = client.get("/api/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ("PatientProfile", "HcpProfile", "RecommendationDetail", "InboxItem"):
        assert name in schemas


# --- Architecture boundaries ----------------------------------------------------------------


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# Future AI infrastructure (V2) lives in these packages; core business logic never imports it.
AI_PACKAGES = ("app.ai", "app.context", "app.llm.providers")
CORE = ("nba", "engagement", "content", "core", "services", "clinical", "commercial", "legal")


def test_core_business_modules_never_import_ai_packages():
    offenders = []
    for package in CORE:
        for path in (APP / package).rglob("*.py"):
            for name in _imports(path):
                if name.startswith(AI_PACKAGES):
                    offenders.append(f"{path.relative_to(APP)} imports {name}")
    assert offenders == []


def test_read_services_have_no_http_or_model_provider_dependency():
    """`app.llm.template` is the deterministic fill of the approved wording (no model); any
    other `app.llm` module is a model provider and stays out of the read services."""
    offenders = []
    for path in (APP / "services").glob("*.py"):
        for name in _imports(path):
            provider = name.startswith("app.llm") and name != "app.llm.template"
            if provider or name.startswith(("fastapi", "starlette", "app.api", *AI_PACKAGES)):
                offenders.append(f"{path.name} imports {name}")
    assert offenders == []
