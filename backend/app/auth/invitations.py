"""Invitations: the only way a professional account (HCP, Medical Representative, Care
Manager, Compliance / MLR) comes into existence, and the clinic path for a patient whose
care manager prepared their record (the invitation is then bound to that record).

Every operation is checked here, on the server, against the onboarding authority in
core/permissions.py (`can_invite`). The role always comes from the stored invitation and
the recipient's email is bound to it; nothing the browser sends can change either.

Lifecycle:

  create   inviter with authority for the role -> pending invitation, email sent
  lookup   anyone holding the link -> who invited them, as what, until when
  accept   form filled in -> pending account with the invitation's role and email, code
           emailed; the invitation is still pending
  verify   (auth/service.verify_otp -> complete_for) code correct and invitation still
           valid -> invitation accepted, account active, professionally verified

Only a hash of each token is stored. Re-issuing replaces the token and revokes the old one,
so at most one link per invitation works at a time.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.errors import AuthError
from app.auth.repository import SqlUserRepository
from app.clinical import hcps, records, vocabulary
from app.core import age, jurisdiction
from app.core.config import get_settings
from app.core.permissions import (
    INVITE_PERMISSION,
    ROLE_LABELS,
    Permission,
    can,
    can_invite,
)
from app.core.security import hash_password, new_token, token_hash
from app.mail.templates import InvitationEmail, invitation_email
from app.mail.transport import get_mailer
from app.models import CareManagerPatient, Invitation, Patient, User
from app.models.enums import (
    AccountSource,
    AccountStatus,
    InvitationStatus,
    PatientOrigin,
    Role,
    VerificationSource,
)
from app.models.tables import utcnow

log = logging.getLogger("nba.invitations")

EMAIL_DELIVERY, DEVELOPMENT, FAILED = "email", "development", "failed"


def _now() -> datetime:
    return utcnow()


def _forbidden() -> AuthError:
    return AuthError(403, "invite_forbidden", "Your account is not permitted to invite this role.")


def _not_found() -> AuthError:
    return AuthError(404, "invitation_not_found", "Invitation not found.")


def _link(token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/invite/{token}"


def refresh(db: Session, inv: Invitation) -> str:
    """The invitation's current status, marking it expired (and auditing that) on first
    sight after its deadline."""
    if inv.status == InvitationStatus.PENDING and _now() >= inv.expires_at:
        inv.status = InvitationStatus.EXPIRED
        audit.record(db, "invitation_expired", "invitation", inv.id, detail={"role": inv.role})
    return inv.status


def _responsible(db: Session, actor: User, patient_id: str | None) -> bool:
    """The care manager is responsible for this patient (it is on their panel)."""
    return patient_id is not None and (
        db.get(CareManagerPatient, (actor.id, patient_id)) is not None
    )


def _record_free(db: Session, patient_id: str | None) -> bool:
    """The bound clinic record still exists and no account uses it yet."""
    patient = db.get(Patient, patient_id) if patient_id else None
    return (
        patient is not None
        and patient.origin == PatientOrigin.CLINIC
        and db.scalar(select(User.id).where(User.patient_id == patient_id)) is None
    )


def _authority_holds(db: Session, inv: Invitation) -> bool:
    """The inviter still exists, is active and may still invite this role (for a patient:
    is still responsible for the bound record, which is still free)."""
    inviter = db.get(User, inv.invited_by_user_id)
    holds = (
        inviter is not None
        and inviter.status == AccountStatus.ACTIVE
        and can_invite(inviter, inv.role)
    )
    if holds and inv.role == Role.PATIENT:
        holds = _responsible(db, inviter, inv.patient_id) and _record_free(db, inv.patient_id)
    return holds


def _send(db: Session, inv: Invitation, token: str) -> str | None:
    """Emails the link. Returns the link only for a local run without a mail server, where
    it is shown to the inviter in a box saying no email was sent."""
    settings = get_settings()
    inviter = db.get(User, inv.invited_by_user_id)
    mailer = get_mailer()
    if mailer is None:
        if not settings.show_development_codes:
            inv.delivery = FAILED
            return None
        inv.delivery = DEVELOPMENT
        log.warning("Development invitation %s created (no email sent)", inv.id)
        return _link(token)
    message = invitation_email(
        InvitationEmail(
            to=inv.email,
            inviter_name=inviter.display_name,
            inviter_role=ROLE_LABELS.get(inv.inviter_role, inv.inviter_role),
            role_label=ROLE_LABELS[inv.role],
            link=_link(token),
            expires_at=inv.expires_at,
            patient=inv.role == Role.PATIENT,
        )
    )
    try:
        mailer.deliver(message)
        inv.delivery = EMAIL_DELIVERY
    except Exception as exc:  # noqa: BLE001 - any delivery failure is reported the same way
        log.error("Could not email invitation %s: %s", inv.id, type(exc).__name__)
        inv.delivery = FAILED
    return None


def _new(
    db: Session,
    *,
    email: str,
    role: str,
    inviter_id: int,
    inviter_role: str,
    patient_id: str | None = None,
    specialties: list[str] | None = None,
):
    token, now = new_token(), _now()
    inv = Invitation(
        token_hash=token_hash(token),
        email=email,
        role=role,
        patient_id=patient_id,
        specialties=specialties,
        invited_by_user_id=inviter_id,
        inviter_role=inviter_role,
        status=InvitationStatus.PENDING,
        created_at=now,
        expires_at=now + timedelta(hours=get_settings().invitation_ttl_hours),
    )
    db.add(inv)
    db.flush()
    return inv, token


def _may_manage(actor: User, inv: Invitation) -> bool:
    """Admin manages every invitation; others only the ones they sent, and only while they
    still hold authority for its role."""
    if can(actor, Permission.INVITATION_READ_ALL):
        return True
    return inv.invited_by_user_id == actor.id and can_invite(actor, inv.role)


def visible(db: Session, actor: User, invitation_id: int) -> Invitation:
    inv = db.get(Invitation, invitation_id)
    if inv is None or not (
        can(actor, Permission.INVITATION_READ_ALL) or inv.invited_by_user_id == actor.id
    ):
        raise _not_found()  # someone else's invitation looks exactly like a missing one
    return inv


# --- Inviter side ------------------------------------------------------------------------


def create(
    db: Session,
    actor: User,
    email: str,
    role: str,
    patient_id: str | None = None,
    specialties: list[str] | None = None,
) -> tuple[Invitation, str | None]:
    if role not in INVITE_PERMISSION:
        raise AuthError(422, "invalid_role", "Choose a role that can be invited.")
    if not can_invite(actor, role):
        raise _forbidden()
    if role == Role.PATIENT:
        # Only from the patient's own record, by the care manager responsible for it.
        if patient_id is None:
            raise AuthError(422, "invite_from_record", "Invite a patient from their record.")
        if not _responsible(db, actor, patient_id):
            raise _forbidden()
        if not _record_free(db, patient_id):
            raise AuthError(
                409,
                "record_in_use",
                "This record already has an account or is not a clinic record.",
            )
    elif patient_id is not None:
        raise AuthError(422, "invalid_role", "Only a patient invitation is bound to a record.")
    if specialties is not None and role != Role.HCP:
        raise AuthError(422, "invalid_specialty", "Only an HCP invitation carries specialties.")
    chosen = hcps.validate_specialties(specialties) if role == Role.HCP else None
    from app.auth.service import validate_email  # local import: service imports this module

    email = validate_email(email)
    existing = SqlUserRepository(db).get_by_email(email)
    if existing is not None and not (
        not existing.verified and existing.source == AccountSource.INVITATION
    ):
        raise AuthError(409, "account_exists", "An account already exists for this email.")

    previous = db.scalars(
        select(Invitation).where(
            Invitation.email == email, Invitation.status == InvitationStatus.PENDING
        )
    ).all()
    still_open = [p for p in previous if refresh(db, p) == InvitationStatus.PENDING]
    for old in still_open:
        if not _may_manage(actor, old):
            raise AuthError(
                409, "invitation_pending", "Someone else has already invited this email."
            )

    inv, token = _new(
        db, email=email, role=role, inviter_id=actor.id, inviter_role=actor.role,
        patient_id=patient_id, specialties=chosen,
    )  # fmt: skip
    for old in still_open:
        _revoke(db, actor, old, replaced_by=inv)
    dev_link = _send(db, inv, token)
    audit.record(
        db, "invitation_created", "invitation", inv.id, actor=actor.username,
        actor_role=actor.role, detail={"role": role, "delivery": inv.delivery},
    )  # fmt: skip
    return inv, dev_link


def _revoke(db: Session, actor: User, inv: Invitation, replaced_by: Invitation | None = None):
    inv.status = InvitationStatus.REVOKED
    inv.revoked_at = _now()
    inv.revoked_by_user_id = actor.id
    inv.replaced_by_id = replaced_by.id if replaced_by else None
    audit.record(
        db, "invitation_revoked", "invitation", inv.id, actor=actor.username,
        actor_role=actor.role, detail={"replaced_by": inv.replaced_by_id},
    )  # fmt: skip


def revoke(db: Session, actor: User, invitation_id: int) -> Invitation:
    inv = visible(db, actor, invitation_id)
    if not _may_manage(actor, inv):
        raise _forbidden()
    if refresh(db, inv) != InvitationStatus.PENDING:
        raise AuthError(409, "invitation_closed", "Only a pending invitation can be revoked.")
    _revoke(db, actor, inv)
    return inv


def reissue(db: Session, actor: User, invitation_id: int) -> tuple[Invitation, str | None]:
    """A fresh link and deadline for the same person and role. The old link stops working."""
    inv = visible(db, actor, invitation_id)
    if not _may_manage(actor, inv):
        raise _forbidden()
    if refresh(db, inv) == InvitationStatus.ACCEPTED:
        raise AuthError(409, "invitation_closed", "This invitation has already been accepted.")
    if not _authority_holds(db, inv):
        raise AuthError(
            409, "invitation_closed", "The original inviter can no longer invite this role."
        )
    existing = SqlUserRepository(db).get_by_email(inv.email)
    if existing is not None and existing.verified:
        raise AuthError(409, "account_exists", "An account already exists for this email.")
    new, token = _new(
        db,
        email=inv.email,
        role=inv.role,
        inviter_id=inv.invited_by_user_id,
        inviter_role=inv.inviter_role,
        patient_id=inv.patient_id,
        specialties=inv.specialties,
    )
    if inv.status == InvitationStatus.PENDING:
        _revoke(db, actor, inv, replaced_by=new)
    else:
        inv.replaced_by_id = new.id
    dev_link = _send(db, new, token)
    audit.record(
        db, "invitation_reissued", "invitation", new.id, actor=actor.username,
        actor_role=actor.role, detail={"replaces": inv.id, "delivery": new.delivery},
    )  # fmt: skip
    return new, dev_link


def revoke_from(db: Session, inviter: User, actor: User) -> int:
    """Revokes every pending invitation an account sent (used when it is disabled)."""
    pending = db.scalars(
        select(Invitation).where(
            Invitation.invited_by_user_id == inviter.id,
            Invitation.status == InvitationStatus.PENDING,
        )
    ).all()
    for inv in pending:
        _revoke(db, actor, inv)
    return len(pending)


# --- Recipient side ------------------------------------------------------------------------

TOKEN_STATES = {
    InvitationStatus.EXPIRED: (
        410,
        "invitation_expired",
        "This invitation has expired. Ask the person who invited you for a new one.",
    ),
    InvitationStatus.ACCEPTED: (
        409,
        "invitation_used",
        "This invitation has already been used. Sign in with your account instead.",
    ),
    InvitationStatus.REVOKED: (
        410,
        "invitation_revoked",
        "This invitation is no longer valid. Ask the person who invited you for a new one.",
    ),
}


def _by_token(db: Session, token: str) -> Invitation:
    """The usable invitation behind a link, or a clear, token-free error."""
    inv = (
        db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token.strip())))
        if token and len(token) <= 128
        else None
    )
    if inv is None:
        raise AuthError(
            404,
            "invitation_invalid",
            "This invitation link isn't valid. Check that you copied the whole link.",
        )
    state = refresh(db, inv)
    inviter = db.get(User, inv.invited_by_user_id)
    context = {"inviter_name": inviter.display_name if inviter else None}
    if state in TOKEN_STATES:
        status, code, message = TOKEN_STATES[state]
        raise AuthError(status, code, message, **context)
    if not _authority_holds(db, inv):
        status, code, message = TOKEN_STATES[InvitationStatus.REVOKED]
        raise AuthError(status, code, message, **context)
    return inv


def lookup(db: Session, token: str) -> dict:
    inv = _by_token(db, token)
    inviter = db.get(User, inv.invited_by_user_id)
    existing = SqlUserRepository(db).get_by_email(inv.email)
    return {
        "email": inv.email,
        "role": inv.role,
        "role_label": ROLE_LABELS[inv.role],
        "inviter_name": inviter.display_name,
        "inviter_role_label": ROLE_LABELS.get(inv.inviter_role, inv.inviter_role),
        "expires_at": inv.expires_at,
        "account_exists": existing is not None and existing.verified,
        "patient": inv.role == Role.PATIENT,
    }


def accept(
    db: Session,
    token: str,
    *,
    name: str,
    date_of_birth: str,
    password: str,
    confirm: str,
    country: str,
    region: str | None,
    accept_terms: bool,
    consent_health_data: bool | None = None,
) -> User:
    """Creates (or refreshes) the pending account for the invited email with the invited
    role. The caller then issues the one-time code to that email."""
    from app.auth.service import (
        record_agreements,
        require_agreements,
        validate_name,
        validate_password,
    )

    inv = _by_token(db, token)
    name = validate_name(name)
    country, region = jurisdiction.validate(country, region)
    dob = age.parse_dob(date_of_birth)
    patient = inv.role == Role.PATIENT
    # Minors may hold a patient account (flagged on the server); professionals may not.
    if not patient and age.is_minor_dob(dob, country, region):
        raise AuthError(
            422,
            "age_requirement",
            "Professional accounts are for adults "
            f"(aged {jurisdiction.adult_age(country, region)} or over where you live).",
        )
    validate_password(password, confirm)
    # A patient also gives the separate, explicit consent to process health information.
    require_agreements(accept_terms, bool(consent_health_data) if patient else None)

    repo = SqlUserRepository(db)
    user = repo.get_by_email(inv.email)
    if user is not None:
        if user.verified or user.source != AccountSource.INVITATION:
            raise AuthError(
                409, "account_exists", "An account already exists for this email. Sign in."
            )
        # An unfinished acceptance (for example on another device): start again. The
        # account was never active, so this is not a role change of a working account.
        user.display_name, user.date_of_birth = name, dob
        user.country, user.region = country, region
        user.password_hash = hash_password(password)
        user.role = inv.role
    else:
        user = repo.create_pending(
            name=name,
            email=inv.email,
            password_hash=hash_password(password),
            role=inv.role,
            source=AccountSource.INVITATION,
        )
        user.date_of_birth = dob
        user.country, user.region = country, region
    inv.claimed_user_id = user.id
    db.flush()
    record_agreements(db, user, "invitation")
    audit.record(
        db, "invitation_claimed", "invitation", inv.id, actor=user.username,
        actor_role=user.role, detail={"user": user.id},
    )  # fmt: skip
    return user


def complete_for(db: Session, user: User) -> Invitation | None:
    """Called when a pending account enters its correct one-time code. For an invited
    account the invitation must still be valid; it is then accepted and the account is
    marked professionally verified. Accounts that are not from an invitation pass through."""
    if user.source != AccountSource.INVITATION:
        return None
    inv = db.scalar(
        select(Invitation)
        .where(Invitation.claimed_user_id == user.id)
        .order_by(Invitation.id.desc())
    )
    valid = (
        inv is not None
        and refresh(db, inv) == InvitationStatus.PENDING
        and inv.email == user.email
        and inv.role == user.role
        and _authority_holds(db, inv)
    )
    if valid and inv.role == Role.PATIENT:
        # The clinic record the care manager prepared becomes this person's record. It
        # follows the residence the person gave; the care manager stays responsible.
        user.patient_id = inv.patient_id
        records.sync_location(db, user)
        user.invited_by_user_id = inv.invited_by_user_id
        inv.status, inv.accepted_at = InvitationStatus.ACCEPTED, _now()
        audit.record(
            db, "invitation_accepted", "invitation", inv.id, actor=user.username,
            actor_role=user.role, detail={"user": user.id, "invited_by": inv.invited_by_user_id},
        )  # fmt: skip
        return inv
    if not valid:
        raise AuthError(
            409,
            "invitation_unavailable",
            "This invitation is no longer valid. Ask the person who invited you for a new one.",
        )
    now = _now()
    inv.status, inv.accepted_at = InvitationStatus.ACCEPTED, now
    if inv.role == Role.HCP and user.hcp_id is None:
        # A new, blank HCP record with exactly the specialties the inviter chose (or none).
        user.hcp_id = hcps.create_hcp(db, user.display_name, inv.specialties or []).hcp_id
    user.professionally_verified = True
    user.professionally_verified_at = now
    user.verification_source = VerificationSource.INVITATION
    user.invited_by_user_id = inv.invited_by_user_id
    audit.record(
        db, "invitation_accepted", "invitation", inv.id, actor=user.username,
        actor_role=user.role, detail={"user": user.id, "invited_by": inv.invited_by_user_id},
    )  # fmt: skip
    return inv


# --- Reading --------------------------------------------------------------------------------


def person(user: User | None) -> dict | None:
    if user is None:
        return None
    return {
        "id": user.id,
        "name": user.display_name,
        "role": user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "professionally_verified": user.professionally_verified,
        "verification_source": user.verification_source,
    }


def out(db: Session, inv: Invitation, viewer: User) -> dict:
    status = refresh(db, inv)
    accepted = db.get(User, inv.claimed_user_id) if status == InvitationStatus.ACCEPTED else None
    return {
        "id": inv.id,
        "email": inv.email,
        "role": inv.role,
        "role_label": ROLE_LABELS[inv.role],
        "status": status,
        "delivery": inv.delivery,
        "created_at": inv.created_at,
        "expires_at": inv.expires_at,
        "accepted_at": inv.accepted_at,
        "revoked_at": inv.revoked_at,
        "replaced_by_id": inv.replaced_by_id,
        "invited_by": person(db.get(User, inv.invited_by_user_id)),
        "inviter_role_label": ROLE_LABELS.get(inv.inviter_role, inv.inviter_role),
        "accepted_user": person(accepted),
        "patient_id": inv.patient_id,
        "specialties": [
            {"code": c, "label": vocabulary.specialty_label(c)} for c in inv.specialties or []
        ],
        # Display hint only; every action is checked again when it is requested.
        "can_manage": _may_manage(viewer, inv) and status != InvitationStatus.ACCEPTED,
    }


def listing(db: Session, actor: User, status: str | None, limit: int, offset: int) -> dict:
    query = select(Invitation)
    if not can(actor, Permission.INVITATION_READ_ALL):
        query = query.where(Invitation.invited_by_user_id == actor.id)
    rows = db.scalars(query.order_by(Invitation.created_at.desc(), Invitation.id.desc())).all()
    items = [out(db, inv, actor) for inv in rows]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    if status:
        items = [i for i in items if i["status"] == status]
    return {"total": len(items), "counts": counts, "items": items[offset : offset + limit]}
