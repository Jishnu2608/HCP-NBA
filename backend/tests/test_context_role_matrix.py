"""Role and record-scope matrix for the shared context layer, through the real read services.

Every subject type is built for every role, as that role's own synthetic account in a fresh
fixture world. The expected outcome comes from the role descriptions in Master_Build section
10 (who may access what), not from the permission map, so a drift between the two shows up
here. Then: assignments removed, permissions revoked, consent changed, content expired, and
collections that mix authorised and unauthorised records.
"""

import pytest
from context_world import ROLES, build, fresh_world, principal
from sqlalchemy import delete, select

from app.ai.aggregate import build_many
from app.ai.contracts import SubjectType
from app.ai.engine import Consumer
from app.core import permissions
from app.models import CareManagerPatient, Consent, Content, Hcp, RepHcp
from app.models.enums import Role
from app.services.errors import Forbidden, NoRecord, NotFound

S = SubjectType
CM, MR, HCP, PAT, COMP, ADMIN = (
    Role.CARE_MANAGER,
    Role.MEDICAL_REP,
    Role.HCP,
    Role.PATIENT,
    Role.COMPLIANCE,
    Role.ADMIN,
)

# Who reads each subject in full (Master_Build section 10). Everyone else: refused (403 for a
# role with no permission of that kind, 404 for a record outside a permitted scope).
EXPECTED_OK: dict[SubjectType, set[str]] = {
    S.PATIENT: {CM, ADMIN},  # assigned patients; admin sees all
    S.HCP: {MR, ADMIN},  # assigned HCPs
    S.RECOMMENDATION: {CM, ADMIN},  # the CM's patient's recommendation
    S.CONTENT: {CM, COMP, ADMIN},  # approved patient content: CM; library: compliance, admin
    S.CARE_REQUEST: {CM, ADMIN},
    S.CARE_QUEUE: {CM},  # care requests are the care manager's work
    S.CONSULTATION_QUEUE: {HCP},  # consultations routed to the HCP
    S.PATIENT_SELF: {PAT},  # one's own record only
    S.CARE_WORKFLOW: {CM, ADMIN},
    S.CONTENT_LINEAGE: {CM, COMP, ADMIN},
    S.QUALITY_MEASURES: {CM, COMP, ADMIN},  # panel for CM; metrics for compliance and admin
    S.ACCESS_SCOPE: {ADMIN},  # account administration
    S.AI_TELEMETRY: {ADMIN},  # AI diagnostics only
    S.AUDIT_ACTIVITY: {COMP, ADMIN},  # audit log readers
    S.JOB_QUEUE: {ADMIN},  # engine operators
}


@pytest.fixture(scope="module")
def world():
    return fresh_world()


@pytest.fixture
def db(world):
    session = world.session_factory()
    yield session
    session.rollback()
    session.close()


def _subject(world, subject_type):
    from app.ai.diagnostics import _fixture_subject

    return _fixture_subject(world, subject_type, "auto")


def _outcome(db, world, subject_type, role) -> str:
    try:
        build(db, principal(db, world, role), subject_type, _subject(world, subject_type))
        return "ok"
    except NotFound:
        return "not_found"
    except NoRecord:
        return "no_record"
    except Forbidden:
        return "forbidden"


def test_every_subject_type_is_covered():
    assert set(EXPECTED_OK) == set(SubjectType)


@pytest.mark.parametrize("subject_type", list(SubjectType), ids=str)
def test_role_matrix(db, world, subject_type):
    got = {role: _outcome(db, world, subject_type, role) for role in ROLES}
    allowed = {role for role, outcome in got.items() if outcome == "ok"}
    assert allowed == EXPECTED_OK[subject_type], got
    # A refusal never builds anything; out of scope looks exactly like missing.
    assert set(got.values()) <= {"ok", "not_found", "forbidden", "no_record"}


def test_admin_diagnostic_rights_never_grant_a_model_operation(db, world):
    from app.ai.context import build as legacy_build
    from app.ai.contracts import ContextRequest

    admin = principal(db, world, ADMIN)
    request = ContextRequest(
        operation="case_brief", subject_type="patient", subject_id=world.subjects["patient"]
    )
    with pytest.raises(Forbidden):
        legacy_build(db, admin, request, permission="ai:case_brief", allowed_kinds=("patient",))


def test_out_of_scope_patient_and_hcp_look_missing(db, world):
    cm = principal(db, world, CM)
    with pytest.raises(NotFound):
        build(db, cm, S.PATIENT, world.subjects["patient_out_of_scope"])
    rep = principal(db, world, MR)
    unassigned = db.scalar(
        select(Hcp.hcp_id)
        .where(Hcp.hcp_id.not_in(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == rep.id)))
        .order_by(Hcp.hcp_id)
    )
    with pytest.raises(NotFound):
        build(db, rep, S.HCP, unassigned)


def test_a_removed_assignment_takes_effect_on_the_next_build(db, world):
    cm = principal(db, world, CM)
    pid = world.subjects["patient"]
    assert build(db, cm, S.PATIENT, pid).model_items
    db.execute(
        delete(CareManagerPatient).where(
            CareManagerPatient.care_manager_user_id == cm.id,
            CareManagerPatient.patient_id == pid,
        )
    )
    with pytest.raises(NotFound):
        build(db, cm, S.PATIENT, pid)
    with pytest.raises(NotFound):
        build(db, cm, S.CARE_REQUEST, world.subjects["care_request"])
    db.rollback()
    assert build(db, cm, S.PATIENT, pid).model_items


def test_a_revoked_permission_takes_effect_on_the_next_build(db, world, monkeypatch):
    cm = principal(db, world, CM)
    reduced = permissions.ROLE_PERMISSIONS[CM] - {permissions.Permission.PATIENT_READ_ASSIGNED}
    monkeypatch.setitem(permissions.ROLE_PERMISSIONS, CM, reduced)
    with pytest.raises(Forbidden):
        build(db, cm, S.PATIENT, world.subjects["patient"])


def test_changed_consent_changes_the_context_but_never_reaches_a_model_view(db, world):
    cm = principal(db, world, CM)
    pid = world.subjects["patient"]
    before = build(db, cm, S.PATIENT, pid)
    for c in db.scalars(select(Consent).where(Consent.patient_id == pid)):
        c.granted = not c.granted
    db.flush()
    after = build(db, cm, S.PATIENT, pid)
    consent = [i.text for i in after.items if i.kind == "consent"]
    assert before.fingerprint != after.fingerprint or not consent
    for ctx in (before, after):
        assert ctx.state_of("consent").value == "excluded_policy"
        assert not any(i.kind == "consent" for i in ctx.model_items)


def test_expired_content_disappears_for_approved_content_readers(db, world):
    cm = principal(db, world, CM)
    cid = world.subjects["content"]
    assert build(db, cm, S.CONTENT, cid).model_items
    content = db.get(Content, cid)
    content.expiry_date = content.effective_date  # expired: no longer usable
    db.flush()
    with pytest.raises(NotFound):
        build(db, cm, S.CONTENT, cid)
    # The library reader (compliance) still sees it, marked not usable.
    ctx = build(db, principal(db, world, COMP), S.CONTENT, cid)
    assert "not usable now" in ctx.model_items[0].text


def test_a_mixed_scope_collection_never_reveals_the_unauthorised_records(db, world):
    cm = principal(db, world, CM)
    own = db.scalars(
        select(CareManagerPatient.patient_id)
        .where(CareManagerPatient.care_manager_user_id == cm.id)
        .order_by(CareManagerPatient.patient_id)
        .limit(2)
    ).all()
    foreign = world.subjects["patient_out_of_scope"]
    consumer = Consumer(id="test", permission=None, categories=("patient", "rx"), model_calls=False)
    many = build_many(db, cm, S.PATIENT, [*own, foreign, "PAT_99999"], consumer)
    assert len(many.members) == 2 and many.out_of_scope == 2 and not many.complete
    blob = " ".join(i.text for m in many.members for i in m.context.items)
    assert foreign not in blob
    assert foreign not in str(many.summary())
    # A role with no patient permission at all is refused outright, not silently emptied.
    with pytest.raises(Forbidden):
        build_many(db, principal(db, world, MR), S.PATIENT, own, consumer)
