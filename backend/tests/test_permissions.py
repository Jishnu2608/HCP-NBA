"""The permission model itself, and proof that no API route is left unguarded."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.permissions import ROLE_HOME, ROLE_PERMISSIONS, SIGNUP_ROLES, can
from app.core.permissions import Permission as P
from app.main import app
from app.models import User
from app.models.enums import Role

# Reachable without signing in. Anything else under /api must refuse an anonymous caller.
PUBLIC = {
    ("GET", "/api/health"),
    ("GET", "/api/meta"),
    ("GET", "/api/auth/roles"),
    ("POST", "/api/auth/signup"),
    ("POST", "/api/auth/verify-otp"),
    ("POST", "/api/auth/resend-otp"),
    ("POST", "/api/auth/login"),
}


def test_every_role_has_a_permission_set_and_a_home():
    assert set(ROLE_PERMISSIONS) == set(Role) == set(ROLE_HOME)
    assert Role.ADMIN not in SIGNUP_ROLES and set(SIGNUP_ROLES) == set(Role) - {Role.ADMIN}


def test_admin_has_every_staff_permission_except_content_approval():
    admin = ROLE_PERMISSIONS[Role.ADMIN]
    self_scoped = {p for p in P if p.startswith("self:")}
    narrower = {
        # Admin holds the wider "all" form of each of these.
        P.PATIENT_READ_ASSIGNED, P.HCP_READ_ASSIGNED, P.NBA_READ_PATIENT_ASSIGNED,
        P.NBA_READ_HCP_ASSIGNED, P.NBA_READ_GATED, P.CONTENT_READ_APPROVED_HCP,
        P.CONTENT_READ_APPROVED_PATIENT,
    }  # fmt: skip
    assert set(P) - admin == self_scoped | narrower | {P.CONTENT_APPROVE}
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
        client = TestClient(app)
        checked = 0
        for path, operations in app.openapi()["paths"].items():
            for method in operations:
                if (method.upper(), path) in PUBLIC:
                    continue
                url = re.sub(r"\{[^}]+\}", "1", path)
                response = client.request(method.upper(), url, json={})
                assert response.status_code == 401, f"{method.upper()} {path} is not protected"
                checked += 1
        assert checked >= 35
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
