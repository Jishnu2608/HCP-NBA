// Names of the permissions the API grants (backend/app/core/permissions.py).
//
// The client never decides what a role may do. It receives the signed-in account's
// permission list from the server and uses it only to choose what to show. Every request
// is authorised again on the server, so changing anything here grants nothing.

export const P = {
  PATIENT_READ_ALL: "patient:read:all",
  PATIENT_READ_ASSIGNED: "patient:read:assigned",
  HCP_READ_ALL: "hcp:read:all",
  HCP_READ_ASSIGNED: "hcp:read:assigned",
  NBA_READ_ALL: "nba:read:all",
  NBA_READ_PATIENT_ASSIGNED: "nba:read:patient_assigned",
  NBA_READ_HCP_ASSIGNED: "nba:read:hcp_assigned",
  NBA_READ_GATED: "nba:read:gated",
  NBA_REVIEW_PATIENT: "nba:review:patient",
  NBA_REVIEW_HCP: "nba:review:hcp",
  CONTENT_READ_ALL: "content:read:all",
  CONTENT_READ_APPROVED_HCP: "content:read:approved_hcp",
  CONTENT_READ_APPROVED_PATIENT: "content:read:approved_patient",
  CONTENT_APPROVE: "content:approve",
  AUDIT_READ: "audit:read",
  AUDIT_READ_IDENTIFIED: "audit:read:identified",
  ANALYTICS_READ: "analytics:read",
  MODELS_READ: "models:read",
  ENGINE_OPERATE: "engine:operate",
  CONFIG_MANAGE: "config:manage",
  USER_MANAGE: "user:manage",
  USER_DELETE: "user:delete",
  PATIENT_CARE_MANAGE: "patient:care:manage",
  SELF_PROFILE_READ: "self:profile:read",
  SELF_INBOX: "self:inbox",
  SELF_CONSENT_MANAGE: "self:consent:manage",
  SELF_PATIENTS_READ: "self:patients:read",
  SELF_HEALTH_MANAGE: "self:health:manage",
  SELF_SPECIALTY_REQUEST: "self:specialty:request",
  SELF_CONSULTATIONS_MANAGE: "self:consultations:manage",
  INVITE_HCP: "invite:hcp",
  INVITE_MEDICAL_REP: "invite:medical_rep",
  INVITE_CARE_MANAGER: "invite:care_manager",
  INVITE_COMPLIANCE: "invite:compliance",
  INVITE_PATIENT: "invite:patient",
  INVITATION_READ_ALL: "invitation:read:all",
  PRIVACY_SELF: "privacy:self",
  PRIVACY_REQUEST: "privacy:request",
  PRIVACY_MANAGE: "privacy:manage",
} as const;

export type Permission = (typeof P)[keyof typeof P];

export const NBA_READ: Permission[] = [
  P.NBA_READ_ALL,
  P.NBA_READ_GATED,
  P.NBA_READ_HCP_ASSIGNED,
  P.NBA_READ_PATIENT_ASSIGNED,
];
export const PATIENT_READ: Permission[] = [P.PATIENT_READ_ALL, P.PATIENT_READ_ASSIGNED];
export const HCP_READ: Permission[] = [P.HCP_READ_ALL, P.HCP_READ_ASSIGNED];
/** Any onboarding authority. Which roles may actually be invited comes from the server. */
export const INVITE_ANY: Permission[] = [
  P.INVITE_HCP,
  P.INVITE_MEDICAL_REP,
  P.INVITE_CARE_MANAGER,
  P.INVITE_COMPLIANCE,
  P.INVITE_PATIENT,
];
export const CONTENT_READ: Permission[] = [
  P.CONTENT_READ_ALL,
  P.CONTENT_READ_APPROVED_HCP,
  P.CONTENT_READ_APPROVED_PATIENT,
];
