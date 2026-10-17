"""One account's access boundary, for an administrator: its role, the permissions the role map
gives it and how many records its assignments cover. No email, name, date of birth or record
ids. Read only; authorised by `user:manage`, like the account screens."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import Permission, can, permissions_for
from app.models import CareManagerPatient, RepHcp, User
from app.services.errors import Forbidden, NotFound
from app.services.schemas import Contract


class AccessScope(Contract):
    role: str
    status: str
    permissions: list[str]
    assigned_patients: int
    assigned_hcps: int
    own_record: bool  # linked to a patient or HCP record of its own


def access_scope(db: Session, user: User, account_id: int) -> AccessScope:
    if not can(user, Permission.USER_MANAGE):
        raise Forbidden("Your account cannot read other accounts.")
    account = db.get(User, account_id)
    if account is None:
        raise NotFound("Account not found")
    return AccessScope(
        role=account.role,
        status=account.status,
        permissions=sorted(p.value for p in permissions_for(account.role)),
        assigned_patients=db.scalar(
            select(func.count()).where(CareManagerPatient.care_manager_user_id == account.id)
        ),
        assigned_hcps=db.scalar(select(func.count()).where(RepHcp.rep_user_id == account.id)),
        own_record=account.patient_id is not None or account.hcp_id is not None,
    )
