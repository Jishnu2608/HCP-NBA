"""Real HCPs' records and every HCP's specialties.

An invited HCP gets a new, blank record: no NPI, practice, prescribing volume, history or
specialty is invented. Specialties come only from an administrator (at invitation or later,
or by approving the HCP's own change request) and only from the controlled list. Synthetic
HCPs keep their generated record and are the only HCPs the engine targets.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.auth.errors import AuthError
from app.clinical import vocabulary
from app.models import (
    Hcp,
    HcpNumber,
    HcpSpecialty,
    PatientHcp,
    SpecialtyChangeRequest,
    User,
)
from app.models.enums import AccountSource
from app.models.tables import utcnow

SYNTHETIC, INVITED = "synthetic", "invited"
PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
ACTIONS = ("add", "remove", "replace")


def is_real(hcp: Hcp | None) -> bool:
    return hcp is not None and hcp.origin != SYNTHETIC


def validate_specialties(values: list[str] | None) -> list[str]:
    """A clean, ordered list from the controlled vocabulary. Unknown values are refused."""
    values = list(dict.fromkeys(v.strip() for v in (values or []) if v and v.strip()))
    unknown = [v for v in values if v not in vocabulary.SPECIALTIES]
    if unknown:
        raise AuthError(422, "invalid_specialty", "Choose specialties from the list.")
    return sorted(values, key=list(vocabulary.SPECIALTIES).index)


def specialties_of(db: Session, hcp_id: str) -> list[str]:
    rows = db.scalars(select(HcpSpecialty.specialty).where(HcpSpecialty.hcp_id == hcp_id))
    return sorted(rows, key=lambda s: list(vocabulary.SPECIALTIES).index(s)
                  if s in vocabulary.SPECIALTIES else 99)  # fmt: skip


def specialty_out(db: Session, hcp_id: str) -> list[dict]:
    return [{"code": s, "label": vocabulary.specialty_label(s)} for s in specialties_of(db, hcp_id)]


def _replace(db: Session, hcp_id: str, values: list[str]) -> None:
    db.query(HcpSpecialty).filter(HcpSpecialty.hcp_id == hcp_id).delete()
    db.add_all(HcpSpecialty(hcp_id=hcp_id, specialty=s) for s in values)
    db.flush()


def set_specialties(db: Session, actor: User, hcp: Hcp, values: list[str], *, why: str) -> dict:
    """Administrator-authorised change. Audited with the previous and the new list."""
    values = validate_specialties(values)
    previous = specialties_of(db, hcp.hcp_id)
    _replace(db, hcp.hcp_id, values)
    audit.record(
        db, "hcp_specialties_changed", "hcp", hcp.hcp_id, actor=actor.username,
        actor_role=actor.role, detail={"previous": previous, "new": values, "via": why},
    )  # fmt: skip
    return {"previous": previous, "new": values}


def new_hcp_id(db: Session) -> str:
    number = HcpNumber(created_at=utcnow())
    db.add(number)
    db.flush()
    return f"HCP_R{number.id:06d}"


def create_hcp(db: Session, name: str, specialties: list[str]) -> Hcp:
    """A blank record for an invited HCP. Only the name and the given specialties."""
    clean = " ".join(name.split())
    for prefix in ("Dr. ", "Dr "):
        clean = clean.removeprefix(prefix)
    first, _, last = clean.partition(" ")
    hcp = Hcp(
        hcp_id=new_hcp_id(db), first_name=first[:64], last_name=last.strip()[:64],
        origin=INVITED, rx_volume_annual=0,
    )  # fmt: skip
    db.add(hcp)
    db.flush()
    _replace(db, hcp.hcp_id, validate_specialties(specialties))
    return hcp


# --- Specialty change requests ------------------------------------------------------------


def _computed(action: str, current: list[str], chosen: list[str]) -> list[str]:
    if action == "add":
        return validate_specialties(current + chosen)
    if action == "remove":
        return validate_specialties([s for s in current if s not in chosen])
    return validate_specialties(chosen)


def request_out(db: Session, r: SpecialtyChangeRequest, *, staff: bool = False) -> dict:
    label = vocabulary.specialty_label
    row = {
        "id": r.id,
        "action": r.action,
        "requested": [{"code": s, "label": label(s)} for s in r.requested],
        "previous": [{"code": s, "label": label(s)} for s in r.previous],
        "notes": r.notes,
        "status": r.status,
        "decision_notes": r.decision_notes,
        "created_at": r.created_at,
        "decided_at": r.decided_at,
    }
    if staff:
        hcp = db.get(Hcp, r.hcp_id)
        requester = db.get(User, r.requested_by_user_id) if r.requested_by_user_id else None
        reviewer = db.get(User, r.reviewer_user_id) if r.reviewer_user_id else None
        row |= {
            "hcp_id": r.hcp_id,
            "hcp_name": f"Dr. {hcp.first_name} {hcp.last_name}".strip() if hcp else None,
            "requested_by": requester.display_name if requester else None,
            "account_id": requester.id if requester else None,
            "reviewer": reviewer.display_name if reviewer else None,
            "current": [{"code": s, "label": label(s)} for s in specialties_of(db, r.hcp_id)],
        }
    return row


def submit_request(
    db: Session, user: User, action: str, chosen: list[str], notes: str | None
) -> SpecialtyChangeRequest:
    if user.hcp_id is None:
        raise AuthError(409, "no_assignment", "No HCP record is linked to this account.")
    if action not in ACTIONS:
        raise AuthError(422, "invalid_action", "Choose add, remove or replace.")
    chosen = validate_specialties(chosen)
    current = specialties_of(db, user.hcp_id)
    requested = _computed(action, current, chosen)
    if requested == current:
        raise AuthError(422, "no_change", "This request would not change your specialties.")
    pending = db.scalar(
        select(SpecialtyChangeRequest.id).where(
            SpecialtyChangeRequest.hcp_id == user.hcp_id, SpecialtyChangeRequest.status == PENDING
        )
    )
    if pending:
        raise AuthError(409, "request_pending", "You already have a request waiting for review.")
    request = SpecialtyChangeRequest(
        hcp_id=user.hcp_id, requested_by_user_id=user.id, action=action, requested=requested,
        previous=current, notes=" ".join((notes or "").split())[:500] or None, status=PENDING,
        created_at=utcnow(),
    )  # fmt: skip
    db.add(request)
    db.flush()
    audit.record(
        db, "specialty_change_requested", "hcp", user.hcp_id, actor=user.username,
        actor_role=user.role,
        detail={
            "request": request.id, "action": action, "previous": current, "requested": requested
        },
    )  # fmt: skip
    return request


def decide(
    db: Session, admin: User, request_id: int, approve: bool, notes: str | None
) -> SpecialtyChangeRequest:
    request = db.get(SpecialtyChangeRequest, request_id)
    if request is None:
        raise AuthError(404, "request_not_found", "Request not found.")
    if request.status != PENDING:
        raise AuthError(409, "request_closed", "This request has already been decided.")
    hcp = db.get(Hcp, request.hcp_id)
    if approve:
        current = specialties_of(db, request.hcp_id)
        if current != list(request.previous):
            # The specialties changed after the request was made: applying it now could undo
            # that change. The administrator rejects it and the HCP asks again.
            raise AuthError(
                409, "request_stale",
                "The specialties changed since this request was made. Reject it so the HCP "
                "can submit a new one.",
            )  # fmt: skip
        set_specialties(db, admin, hcp, list(request.requested), why=f"request {request.id}")
    request.status = APPROVED if approve else REJECTED
    request.reviewer_user_id = admin.id
    request.decision_notes = " ".join((notes or "").split())[:500] or None
    request.decided_at = utcnow()
    audit.record(
        db, "specialty_change_" + request.status, "hcp", request.hcp_id, actor=admin.username,
        actor_role=admin.role,
        detail={"request": request.id, "previous": request.previous,
                "requested": request.requested},
    )  # fmt: skip
    db.flush()
    return request


# --- Accounts invited before HCP records were separated ------------------------------------


def separate_real_hcps(db: Session) -> int:
    """Invited HCP accounts still linked to a synthetic HCP get their own blank record (no
    specialty); the synthetic record gets its generated name back. Idempotent."""
    from app.datagen.generate import synthetic_hcp_names

    moved = [
        u
        for u in db.scalars(
            select(User).where(User.hcp_id.is_not(None), User.source == AccountSource.INVITATION)
        )
        if not is_real(db.get(Hcp, u.hcp_id))
    ]
    if not moved:
        return 0
    names = synthetic_hcp_names([u.hcp_id for u in moved])
    for user in moved:
        old = db.get(Hcp, user.hcp_id)
        if old.hcp_id in names:
            old.first_name, old.last_name = names[old.hcp_id]
        hcp = create_hcp(db, user.display_name, [])
        user.hcp_id = hcp.hcp_id
        audit.record(
            db, "hcp_record_separated", "user", user.id,
            detail={"previous_record": old.hcp_id, "new_record": hcp.hcp_id},
        )  # fmt: skip
    db.flush()
    return len(moved)


# --- Surviving a demo reset ----------------------------------------------------------------


def snapshot(db: Session) -> dict:
    emails = dict(db.execute(select(User.id, User.email)).all())
    ids = list(db.scalars(select(Hcp.hcp_id).where(Hcp.origin != SYNTHETIC)))
    if not ids:
        return {}

    def row(obj) -> dict:
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    return {
        "hcps": [row(h) for h in db.scalars(select(Hcp).where(Hcp.hcp_id.in_(ids)))],
        "specialties": [
            row(s) for s in db.scalars(select(HcpSpecialty).where(HcpSpecialty.hcp_id.in_(ids)))
        ],
        "links": [
            row(p) for p in db.scalars(select(PatientHcp).where(PatientHcp.hcp_id.in_(ids)))
        ],
        "requests": [
            {"row": row(r), "users": {c: emails.get(getattr(r, c))
                                      for c in ("requested_by_user_id", "reviewer_user_id")}}
            for r in db.scalars(
                select(SpecialtyChangeRequest).where(SpecialtyChangeRequest.hcp_id.in_(ids))
            )
        ],
    }  # fmt: skip


def restore(db: Session, saved: dict) -> None:
    """Real HCPs and their specialties, before accounts are restored."""
    if not saved:
        return
    db.add_all(Hcp(**h) for h in saved["hcps"])
    db.flush()
    db.add_all(HcpSpecialty(**s) for s in saved["specialties"])
    db.flush()


def restore_links(db: Session, saved: dict) -> None:
    """Patient links (patients exist again by now) and change requests (accounts too)."""
    if not saved:
        return
    from app.models import Patient

    for link in saved["links"]:
        if db.get(Patient, link["patient_id"]) and not db.get(
            PatientHcp, (link["patient_id"], link["hcp_id"])
        ):
            db.add(PatientHcp(**link))
    ids = dict(db.execute(select(User.email, User.id)).all())
    for item in saved["requests"]:
        row = dict(item["row"])
        row.pop("id")
        for column, email in item["users"].items():
            row[column] = ids.get(email)
        db.add(SpecialtyChangeRequest(**row))
    db.flush()


def pending_for(db: Session, hcp_id: str) -> int | None:
    """The id of the HCP's pending change request, if any."""
    return db.scalar(
        select(SpecialtyChangeRequest.id).where(
            SpecialtyChangeRequest.hcp_id == hcp_id, SpecialtyChangeRequest.status == PENDING
        )
    )


def real_hcp_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(Hcp).where(Hcp.origin != SYNTHETIC))
