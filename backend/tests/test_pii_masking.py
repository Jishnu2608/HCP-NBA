"""Role-based patient PII masking, end to end over HTTP.

Two synthetic canary patients are planted:

- "Gayatri Saikia": assigned to care manager cm01, on hcp0001's care team, with a portal account
  (pat00001), a canary birth date and ZIP, and her name inside a care note, a care request
  reason and the message drafts the engine writes for her. Only cm01, hcp0001 and the patient
  herself may see any of it unmasked.
- "Zorawar Pemberton": assigned to nobody. Nobody may see it unmasked; Admin sees it masked.

Every GET route of the API is called as every role, plus name searches. A canary value in any
response to a role that may not see it unmasked is a leak. Masked forms (G****** S*****) are
expected wherever a role may see the record at all.
"""

import re

import pytest
from conftest import ApiClient, auth, new_session
from sqlalchemy import delete, select

from app import cycle
from app.core.db import get_db
from app.datagen.generate import GenConfig, generate
from app.main import app
from app.models import CareManagerPatient, CareNote, CareRequest, Patient, PatientHcp, User
from app.models.enums import CareNoteKind
from app.models.tables import utcnow

GAYATRI = {"first": "Gayatri", "last": "Saikia", "dob": "1987-03-14", "zip": "78245"}
ZORAWAR = {"first": "Zorawar", "last": "Pemberton", "dob": "1979-11-02", "zip": "99501"}
ROLES = ["admin", "cm01", "cm02", "hcp0001", "hcp0002", "rep01", "compliance1", "pat00001",
         "pat00002"]  # fmt: skip
GAYATRI_ALLOWED = {"cm01", "hcp0001", "pat00001"}


@pytest.fixture(scope="module")
def world():
    from datetime import date

    db = new_session()
    generate(db, GenConfig(seed=43, n_patients=60, n_hcps=12, n_reps=2, n_care_managers=2))
    db.commit()
    users = {u.username: u for u in db.scalars(select(User))}
    cm1, cm2 = users["cm01"], users["cm02"]
    h1, h2 = users["hcp0001"].hcp_id, users["hcp0002"].hcp_id
    g_user = users["pat00001"]
    g = db.get(Patient, g_user.patient_id)
    g.first_name, g.last_name = GAYATRI["first"], GAYATRI["last"]
    g.birth_date, g.zip = date.fromisoformat(GAYATRI["dob"]), GAYATRI["zip"]
    g_user.display_name = f"{GAYATRI['first']} {GAYATRI['last']}"
    db.execute(delete(CareManagerPatient).where(CareManagerPatient.patient_id == g.patient_id))
    db.add(CareManagerPatient(care_manager_user_id=cm1.id, patient_id=g.patient_id))
    db.execute(delete(PatientHcp).where(PatientHcp.patient_id == g.patient_id))
    db.add(PatientHcp(patient_id=g.patient_id, hcp_id=h1, is_primary=True))
    taken = {g.patient_id}
    if users.get("pat00002") is not None:
        taken.add(users["pat00002"].patient_id)
    z = db.scalar(
        select(Patient).where(Patient.patient_id.not_in(taken)).order_by(Patient.patient_id)
    )
    z.first_name, z.last_name = ZORAWAR["first"], ZORAWAR["last"]
    z.birth_date, z.zip = date.fromisoformat(ZORAWAR["dob"]), ZORAWAR["zip"]
    db.execute(delete(CareManagerPatient).where(CareManagerPatient.patient_id == z.patient_id))
    db.execute(delete(PatientHcp).where(PatientHcp.patient_id == z.patient_id))
    db.flush()
    cycle.run(db)  # drafts are written with the canary first names
    now = utcnow()
    request = CareRequest(
        patient_id=g.patient_id, type="consultation", status="awaiting_hcp",
        reason="Gayatri Saikia asks whether the refill can wait", assigned_hcp_id=h1,
        routed_at=now, created_at=now, updated_at=now,
    )  # fmt: skip
    db.add(request)
    db.flush()
    db.add(CareNote(patient_id=g.patient_id, author_user_id=cm1.id, kind=CareNoteKind.FOLLOW_UP,
                    text="Spoke with Gayatri; Saikia family prefers mornings.",
                    visible_to_patient=False, request_id=request.id, created_at=now))  # fmt: skip
    db.commit()
    assert h2 != h1 and cm2.id != cm1.id
    app.dependency_overrides[get_db] = lambda: db
    client = ApiClient(app)
    yield {"client": client, "db": db, "g": g.patient_id, "z": z.patient_id,
           "g_user": g_user.id, "h1": h1}  # fmt: skip
    app.dependency_overrides.clear()
    db.close()


def _paths(world) -> list[str]:
    db = world["db"]
    from app.models import Content, Nba

    nba = db.scalar(select(Nba.id).where(Nba.target_id == world["g"]).order_by(Nba.id))
    content = db.scalar(select(Content.content_id).order_by(Content.content_id))
    fill = {
        "user_id": str(world["g_user"]), "nba_id": str(nba or 1), "hcp_id": world["h1"],
        "content_id": content, "job_id": "1", "operation": "case_brief", "kind": "terms",
    }  # fmt: skip
    out = []
    for path, ops in app.openapi()["paths"].items():
        if "get" not in ops or path.startswith("/api/docs") or path == "/api/openapi.json":
            continue
        if "{patient_id}" in path:
            out += [path.replace("{patient_id}", world["g"]),
                    path.replace("{patient_id}", world["z"])]  # fmt: skip
            continue
        out.append(re.sub(r"{(\w+)}", lambda m: fill[m.group(1)], path))
    out += [
        "/api/patients?q=Gayatri", "/api/patients?q=Saikia", "/api/patients?q=Zorawar",
        "/api/admin/users?q=Gayatri", "/api/admin/users?q=pat00001",
        "/api/integration/crm/records?format=csv", "/api/integration/crm/records",
        f"/api/audit?entity_id={world['g']}",
    ]  # fmt: skip
    return out


def _canaries(world) -> list[tuple[str, set[str], list[str]]]:
    g = [GAYATRI["first"], GAYATRI["last"], GAYATRI["dob"], GAYATRI["zip"], "pat00001@nba.demo"]
    z = [ZORAWAR["first"], ZORAWAR["last"], ZORAWAR["dob"], ZORAWAR["zip"]]
    return [("Gayatri", GAYATRI_ALLOWED, g), ("Zorawar", set(), z)]


def test_no_role_receives_pii_it_may_not_see(world):
    client = world["client"]
    leaks = []
    for role in ROLES:
        headers = auth(client, role)
        for path in _paths(world):
            r = client.get(path, headers=headers)
            if r.status_code >= 400:
                continue
            body = r.text
            for label, allowed, values in _canaries(world):
                if role in allowed:
                    continue
                hits = [v for v in values if re.search(rf"(?<![\w*]){re.escape(v)}(?!\w)", body)]
                if hits:
                    leaks.append(f"{role} {path}: {label} {hits}")
    assert not leaks, "\n" + "\n".join(sorted(set(leaks)))


# --- Masked and unmasked forms --------------------------------------------------------------------


def _get(world, role, path):
    r = world["client"].get(path, headers=auth(world["client"], role))
    assert r.status_code == 200, (role, path, r.text)
    return r.json()


def test_assigned_readers_see_the_full_name(world):
    g = world["g"]
    profile = _get(world, "cm01", f"/api/patients/{g}")
    assert profile["name"] == "Gayatri Saikia"
    assert "Spoke with Gayatri" in str(profile["health"])
    mine = _get(world, "hcp0001", "/api/me/patients")
    assert "Gayatri Saikia" in {p["name"] for p in mine}
    consults = _get(world, "hcp0001", "/api/me/consultations")
    assert "Gayatri Saikia" in str(consults)
    assert _get(world, "pat00001", "/api/me/profile")["name"] == "Gayatri Saikia"
    found = _get(world, "cm01", "/api/patients?q=Gayatri")
    assert [i["name"] for i in found["items"]] == ["Gayatri Saikia"]


def test_admin_sees_masked_values_and_can_still_work_by_id(world):
    g, z = world["g"], world["z"]
    profile = _get(world, "admin", f"/api/patients/{g}")
    assert profile["name"] == "G****** S*****" and profile["patient_id"] == g
    assert "Spoke with G******; S***** family" in str(profile["health"])
    assert _get(world, "admin", f"/api/patients/{z}")["name"] == "Z****** P********"
    account = _get(world, "admin", f"/api/admin/users/{world['g_user']}")
    assert account["name"] == "G****** S*****" and account["email"] == "p*******@n**.demo"
    assert account["patient"]["name"] == "G****** S*****"
    assert _get(world, "admin", "/api/patients?q=Gayatri")["total"] == 0
    assert _get(world, "admin", "/api/patients?q=Saikia")["total"] == 0
    assert _get(world, "admin", "/api/admin/users?q=pat00001")["total"] == 0
    by_id = _get(world, "admin", f"/api/patients?q={g}")
    assert [i["name"] for i in by_id["items"]] == ["G****** S*****"]


def test_unassigned_staff_cannot_find_the_patient_by_name(world):
    for role in ("cm02",):
        assert _get(world, role, "/api/patients?q=Gayatri")["total"] == 0


def test_recommendation_wording_is_masked_for_admin(world):
    from app.models import Nba

    nba = world["db"].scalar(select(Nba.id).where(Nba.target_id == world["g"]).order_by(Nba.id))
    if nba is None:
        pytest.skip("no recommendation for the canary patient in this world")
    admin = _get(world, "admin", f"/api/nba/{nba}")
    assert admin["target_name"] == "G****** S*****"
    assert "Gayatri" not in str(admin)
    cm = _get(world, "cm01", f"/api/nba/{nba}")
    assert cm["target_name"] == "Gayatri Saikia"


# --- Policy unit tests ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,masked",
    [
        ("Gayatri Saikia", "G****** S*****"),
        ("Al", "A*"),
        ("X", "X"),
        ("Mary-Jane O'Neil", "M******** O*****"),
        ("  Ana   Lee ", "  A**   L** "),
        ("", ""),
    ],
)
def test_mask_name_format(name, masked):
    from app.core.pii import mask_name

    assert mask_name(name) == masked
    assert mask_name(None) is None


def test_mask_email_and_text():
    from app.core.pii import mask_email, mask_in_text

    assert mask_email("pat00001@nba.demo") == "p*******@n**.demo"
    assert mask_email("gayatri.s@mail.example.com") == "g********@m***.e******.com"
    assert mask_in_text("GAYATRI called; saikia later. Gay", ["Gayatri Saikia"]) == (
        "G****** called; s***** later. Gay"
    )


def test_viewer_follows_assignments_not_roles(world):
    from app.core.pii import PatientViewer
    from app.models import CareRequest

    db = world["db"]
    users = {u.username: u for u in db.scalars(select(User))}
    g = world["g"]
    assert PatientViewer(db, users["cm01"]).sees(g)
    assert not PatientViewer(db, users["cm02"]).sees(g)
    assert not PatientViewer(db, users["admin"]).sees(g)  # patient:read:all is not PII access
    assert not PatientViewer(db, users["rep01"]).sees(g)
    assert PatientViewer(db, users["pat00001"]).sees(g)
    assert not PatientViewer(db, users["pat00002"]).sees(g)
    assert not PatientViewer(db, None).sees(g)
    # Assignment removed: masked from then on.
    db.execute(delete(CareManagerPatient).where(CareManagerPatient.patient_id == g))
    db.flush()
    assert not PatientViewer(db, users["cm01"]).sees(g)
    db.rollback()
    assert PatientViewer(db, users["cm01"]).sees(g)
    # HCP: care team or a consultation routed now. Off the team with the consultation closed:
    # masked.
    db.execute(delete(PatientHcp).where(PatientHcp.patient_id == g))
    db.flush()
    assert PatientViewer(db, users["hcp0001"]).sees(g)  # still routed
    for r in db.scalars(select(CareRequest).where(CareRequest.patient_id == g)):
        r.status = "closed"
    db.flush()
    assert not PatientViewer(db, users["hcp0001"]).sees(g)
    db.rollback()
    assert PatientViewer(db, users["hcp0001"]).sees(g)


# --- AI context and diagnostics -------------------------------------------------------------------


def test_ai_context_and_diagnostics_never_carry_the_name_to_a_masked_reader(world):
    from app.ai.contracts import ContextRequest
    from app.ai.engine import build_context, diagnostics_consumer
    from app.ai.sources import source_for

    client, db, g = world["client"], world["db"], world["g"]
    headers = auth(client, "admin")
    r = client.post(
        "/api/ai/diagnostics/context",
        json={"mode": "record_preview", "subject_type": "patient", "subject": g},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert "Gayatri" not in r.text and "Saikia" not in r.text
    # The context itself, built for the administrator: reader notes and items masked.
    admin = db.scalar(select(User).where(User.username == "admin"))
    request = ContextRequest(operation="diagnostics", subject_type="patient", subject_id=g)
    ctx = build_context(db, admin, request, diagnostics_consumer(source_for("patient")))
    blob = " ".join([*(i.text for i in ctx.items), *(n.text for n in ctx.reader_notes)])
    assert "Gayatri" not in blob and "Saikia" not in blob
    # The care manager's Case Brief context: names never in what a model may read.
    cm = db.scalar(select(User).where(User.username == "cm01"))
    from app.ai.engine import consumer_for
    from app.ai.operations import case_brief

    ctx = build_context(
        db, cm, ContextRequest(operation="case_brief", subject_type="patient", subject_id=g),
        consumer_for(case_brief.SPEC),
    )  # fmt: skip
    assert not any("Gayatri" in i.text or "Saikia" in i.text for i in ctx.model_items)
    assert "Gayatri" in ctx.identifiers and "Saikia" in ctx.identifiers
