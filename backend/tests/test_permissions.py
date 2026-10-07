"""The permission model itself, and proof that no API route is left unguarded."""

import re
from pathlib import Path

import pytest
from conftest import ApiClient

from app.core.db import get_db
from app.core.permissions import (
    INVITE_PERMISSION,
    PROFESSIONAL_ROLES,
    PUBLIC_SIGNUP_ROLE,
    ROLE_HOME,
    ROLE_PERMISSIONS,
    can,
    can_invite,
)
from app.core.permissions import Permission as P
from app.main import app
from app.models import User
from app.models.enums import Role

# Reachable without signing in. Anything else under /api must refuse an anonymous caller.
PUBLIC = {
    ("GET", "/api/health"),
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/verify-otp"),
    ("POST", "/api/auth/resend-otp"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/invitations/lookup"),
    ("POST", "/api/invitations/accept"),
    # Legal documents are readable by anyone, before signing up.
    ("GET", "/api/legal/documents"),
    ("GET", "/api/legal/documents/{kind}"),
    ("GET", "/api/legal/jurisdictions"),
}


def test_every_role_has_a_permission_set_and_a_home():
    assert set(ROLE_PERMISSIONS) == set(Role) == set(ROLE_HOME)


def test_only_patients_self_register_and_professionals_need_an_invitation():
    assert PUBLIC_SIGNUP_ROLE == Role.PATIENT
    assert PROFESSIONAL_ROLES == {Role.HCP, Role.MEDICAL_REP, Role.CARE_MANAGER, Role.COMPLIANCE}
    assert Role.ADMIN not in INVITE_PERMISSION
    # Patients may also be invited, but only by a care manager (the clinic path).
    assert Role.PATIENT not in PROFESSIONAL_ROLES


# The onboarding authority matrix, spelled out once. Everything not listed is forbidden.
AUTHORITY = {
    Role.ADMIN: {Role.HCP, Role.MEDICAL_REP, Role.CARE_MANAGER, Role.COMPLIANCE},
    Role.HCP: {Role.MEDICAL_REP, Role.CARE_MANAGER},
    Role.CARE_MANAGER: {Role.PATIENT},
}


@pytest.mark.parametrize("inviter", list(Role))
@pytest.mark.parametrize("target", list(Role))
def test_onboarding_authority_matrix(inviter, target):
    expected = target in AUTHORITY.get(inviter, set())
    assert can_invite(User(role=inviter), target) is expected


def test_invite_authority_grants_no_data_access():
    """An HCP who may invite a rep gains nothing a rep can see."""
    hcp = User(role=Role.HCP)
    assert not any(
        can(hcp, p) for p in P if p.startswith(("patient:", "hcp:", "nba:", "content:", "audit"))
    )


def test_admin_has_every_staff_permission_except_content_approval():
    admin = ROLE_PERMISSIONS[Role.ADMIN]
    self_scoped = {p for p in P if p.startswith("self:")}
    narrower = {
        # Admin holds the wider "all" form of each of these.
        P.PATIENT_READ_ASSIGNED, P.HCP_READ_ASSIGNED, P.NBA_READ_PATIENT_ASSIGNED,
        P.NBA_READ_HCP_ASSIGNED, P.NBA_READ_GATED, P.CONTENT_READ_APPROVED_HCP,
        P.CONTENT_READ_APPROVED_PATIENT,
    }  # fmt: skip
    # Care management and inviting clinic patients stay with the care manager responsible.
    care = {P.PATIENT_CARE_MANAGE, P.INVITE_PATIENT}
    # The administrator handles privacy requests and does not submit their own.
    care |= {P.PRIVACY_REQUEST}
    # Content decisions belong to Compliance and proposals to representatives (separation of
    # duties: the administrator neither writes nor approves governed material).
    content = {P.CONTENT_APPROVE, P.CONTENT_PROPOSE}
    assert set(P) - admin == self_scoped | narrower | care | content
    assert [r for r, perms in ROLE_PERMISSIONS.items() if P.CONTENT_APPROVE in perms] == [
        Role.COMPLIANCE
    ]


@pytest.mark.parametrize(
    ("role", "has", "lacks"),
    [
        (Role.PATIENT, {P.SELF_PROFILE_READ, P.SELF_CONSENT_MANAGE, P.SELF_INBOX},
         {P.PATIENT_READ_ASSIGNED, P.NBA_READ_ALL, P.AUDIT_READ, P.USER_MANAGE}),
        (Role.HCP, {P.SELF_PROFILE_READ, P.SELF_PATIENTS_READ, P.SELF_INBOX},
         {P.HCP_READ_ASSIGNED, P.SELF_CONSENT_MANAGE, P.CONTENT_READ_ALL}),
        (Role.CARE_MANAGER, {P.PATIENT_READ_ASSIGNED, P.NBA_REVIEW_PATIENT},
         {P.PATIENT_READ_ALL, P.HCP_READ_ASSIGNED, P.NBA_REVIEW_HCP, P.ENGINE_OPERATE}),
        (Role.MEDICAL_REP, {P.HCP_READ_ASSIGNED, P.NBA_REVIEW_HCP, P.CONTENT_READ_APPROVED_HCP},
         {P.PATIENT_READ_ASSIGNED, P.NBA_REVIEW_PATIENT, P.CONTENT_APPROVE, P.AUDIT_READ}),
        (Role.COMPLIANCE, {P.CONTENT_APPROVE, P.AUDIT_READ, P.NBA_READ_GATED},
         {P.NBA_REVIEW_PATIENT, P.NBA_REVIEW_HCP, P.PATIENT_READ_ALL, P.USER_MANAGE}),
    ],
)  # fmt: skip
def test_role_permissions(role, has, lacks):
    user = User(role=role)
    assert all(can(user, p) for p in has)
    assert not any(can(user, p) for p in lacks)


def test_unknown_role_has_no_permissions():
    assert not any(can(User(role="superuser"), p) for p in P)


def test_every_api_route_refuses_anonymous_callers(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = ApiClient(app)  # passes the CSRF check, so the auth check is what answers
        checked = 0
        for path, operations in app.openapi()["paths"].items():
            for method in operations:
                if (method.upper(), path) in PUBLIC:
                    continue
                url = re.sub(r"\{[^}]+\}", "1", path)
                response = client.request(method.upper(), url, json={})
                assert response.status_code == 401, f"{method.upper()} {path} is not protected"
                checked += 1
        assert checked >= 40
    finally:
        app.dependency_overrides.clear()


def test_role_names_are_not_used_for_authorization_outside_the_permission_map():
    """Authorization asks for permissions. A role comparison in a router or in the scoping
    code would be a second, hidden source of truth."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    # `user.role == ...` on an account object is an authorization decision by role name.
    # `User.role == role` on the column (a list filter) is a data query and is fine.
    pattern = re.compile(r"\b[a-z_]+\.role\s*(==|!=|in\b|not in\b)|require_roles|Role\.[A-Z_]+")
    offenders = []
    for path in [*(app_dir / "api").glob("*.py"), app_dir / "core" / "rbac.py"]:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == []
