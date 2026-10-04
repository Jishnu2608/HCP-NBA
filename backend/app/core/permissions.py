"""The permission model: account -> role -> permission set.

This is the only place where a role is mapped to what it may do. Endpoints ask for a
permission, never for a role name, so adding or reshaping a role is an edit to
ROLE_PERMISSIONS and nothing else.

Which records a permission applies to (data scope) is decided separately, by the
assignment tables and the filters in core/rbac.py.
"""

from enum import StrEnum

from app.models import User
from app.models.enums import Role


class Permission(StrEnum):
    # Profiles
    PATIENT_READ_ALL = "patient:read:all"
    PATIENT_READ_ASSIGNED = "patient:read:assigned"
    HCP_READ_ALL = "hcp:read:all"
    HCP_READ_ASSIGNED = "hcp:read:assigned"
    # Recommendations
    NBA_READ_ALL = "nba:read:all"
    NBA_READ_PATIENT_ASSIGNED = "nba:read:patient_assigned"
    NBA_READ_HCP_ASSIGNED = "nba:read:hcp_assigned"
    # Blocked or held-back recommendations only, with the person's identity hidden.
    NBA_READ_GATED = "nba:read:gated"
    NBA_REVIEW_PATIENT = "nba:review:patient"
    NBA_REVIEW_HCP = "nba:review:hcp"
    # Content
    CONTENT_READ_ALL = "content:read:all"
    CONTENT_READ_APPROVED_HCP = "content:read:approved_hcp"
    CONTENT_READ_APPROVED_PATIENT = "content:read:approved_patient"
    CONTENT_APPROVE = "content:approve"
    # Governance
    AUDIT_READ = "audit:read"
    # Audit detail with account emails and patient / HCP ids left in (others see them masked).
    AUDIT_READ_IDENTIFIED = "audit:read:identified"
    ANALYTICS_READ = "analytics:read"
    MODELS_READ = "models:read"
    ENGINE_OPERATE = "engine:operate"
    CONFIG_MANAGE = "config:manage"
    USER_MANAGE = "user:manage"
    # A person's own record
    SELF_PROFILE_READ = "self:profile:read"
    SELF_INBOX = "self:inbox"
    SELF_CONSENT_MANAGE = "self:consent:manage"
    SELF_PATIENTS_READ = "self:patients:read"
    # Onboarding authority: who may invite whom. One permission per target role, so the
    # whole authority matrix is the set of these permissions in ROLE_PERMISSIONS below.
    # It grants no data access; it is not an organisational hierarchy.
    INVITE_HCP = "invite:hcp"
    INVITE_MEDICAL_REP = "invite:medical_rep"
    INVITE_CARE_MANAGER = "invite:care_manager"
    INVITE_COMPLIANCE = "invite:compliance"
    # See every invitation, not only the ones you sent.
    INVITATION_READ_ALL = "invitation:read:all"
    # Privacy: every account manages its own documents, consents, requests and export;
    # handling other people's privacy requests is separate.
    PRIVACY_SELF = "privacy:self"
    PRIVACY_MANAGE = "privacy:manage"


P = Permission

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    # Everything a member of staff can do, except approving content: MLR approval stays
    # with Compliance so that the person running the engine cannot clear their own content.
    Role.ADMIN: frozenset(
        {
            P.PATIENT_READ_ALL,
            P.HCP_READ_ALL,
            P.NBA_READ_ALL,
            P.NBA_REVIEW_PATIENT,
            P.NBA_REVIEW_HCP,
            P.CONTENT_READ_ALL,
            P.AUDIT_READ,
            P.ANALYTICS_READ,
            P.MODELS_READ,
            P.ENGINE_OPERATE,
            P.CONFIG_MANAGE,
            P.USER_MANAGE,
            P.AUDIT_READ_IDENTIFIED,
            # Admin may onboard every professional role, MLR reviewers included.
            P.INVITE_HCP,
            P.INVITE_MEDICAL_REP,
            P.INVITE_CARE_MANAGER,
            P.INVITE_COMPLIANCE,
            P.INVITATION_READ_ALL,
            P.PRIVACY_SELF,
            P.PRIVACY_MANAGE,
        }
    ),
    Role.COMPLIANCE: frozenset(
        {
            P.CONTENT_READ_ALL,
            P.CONTENT_APPROVE,
            P.NBA_READ_GATED,
            P.AUDIT_READ,
            P.ANALYTICS_READ,
            P.MODELS_READ,
            P.PRIVACY_SELF,
        }
    ),
    Role.MEDICAL_REP: frozenset(
        {
            P.HCP_READ_ASSIGNED,
            P.NBA_READ_HCP_ASSIGNED,
            P.NBA_REVIEW_HCP,
            P.CONTENT_READ_APPROVED_HCP,
            P.PRIVACY_SELF,
        }
    ),
    Role.CARE_MANAGER: frozenset(
        {
            P.PATIENT_READ_ASSIGNED,
            P.NBA_READ_PATIENT_ASSIGNED,
            P.NBA_REVIEW_PATIENT,
            P.CONTENT_READ_APPROVED_PATIENT,
            P.PRIVACY_SELF,
        }
    ),
    # An HCP may bring in the representative and care manager they work with. Never an MLR
    # reviewer (governance stays with the administrator) and never another HCP.
    Role.HCP: frozenset(
        {
            P.SELF_PROFILE_READ,
            P.SELF_INBOX,
            P.SELF_PATIENTS_READ,
            P.INVITE_MEDICAL_REP,
            P.INVITE_CARE_MANAGER,
            P.PRIVACY_SELF,
        }
    ),
    Role.PATIENT: frozenset(
        {P.SELF_PROFILE_READ, P.SELF_INBOX, P.SELF_CONSENT_MANAGE, P.PRIVACY_SELF}
    ),
}

# The only role anyone may register as without an invitation.
PUBLIC_SIGNUP_ROLE = Role.PATIENT

# Roles that can only be granted by an invitation, and the permission that allows inviting
# each one. The administrator is never invited or self-created.
INVITE_PERMISSION: dict[str, Permission] = {
    Role.HCP: P.INVITE_HCP,
    Role.MEDICAL_REP: P.INVITE_MEDICAL_REP,
    Role.CARE_MANAGER: P.INVITE_CARE_MANAGER,
    Role.COMPLIANCE: P.INVITE_COMPLIANCE,
}
PROFESSIONAL_ROLES = frozenset(INVITE_PERMISSION)

ROLE_LABELS: dict[str, str] = {
    Role.ADMIN: "Administrator",
    Role.HCP: "Healthcare Professional",
    Role.MEDICAL_REP: "Medical Representative",
    Role.CARE_MANAGER: "Care Manager",
    Role.COMPLIANCE: "Compliance / MLR Reviewer",
    Role.PATIENT: "Patient",
}

ROLE_DESCRIPTIONS: dict[str, str] = {
    Role.HCP: "Views their own HCP information and relevant engagement and content.",
    Role.MEDICAL_REP: "Works with assigned HCP engagement and next-best-action recommendations.",
    Role.CARE_MANAGER: "Works with assigned patient adherence cases.",
    Role.COMPLIANCE: "Reviews content approval, compliance decisions and audit history.",
}

# Where each role lands after signing in.
ROLE_HOME: dict[str, str] = {
    Role.ADMIN: "/dashboard",
    Role.CARE_MANAGER: "/queue",
    Role.MEDICAL_REP: "/queue",
    Role.COMPLIANCE: "/content",
    Role.PATIENT: "/medications",
    Role.HCP: "/inbox",
}


def permissions_for(role: str) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def can(user: User, permission: Permission) -> bool:
    return permission in permissions_for(user.role)


def can_any(user: User, *permissions: Permission) -> bool:
    return any(can(user, p) for p in permissions)


def can_invite(user: User, role: str) -> bool:
    """The onboarding authority check. False for any role that is not invitable at all."""
    permission = INVITE_PERMISSION.get(role)
    return permission is not None and can(user, permission)


def invitable_roles(user: User) -> list[str]:
    return [role for role in INVITE_PERMISSION if can_invite(user, role)]
