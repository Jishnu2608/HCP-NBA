"""Real patients' records: creation, the care manager responsible, and keeping them apart
from the synthetic demo population.

A patient account owns a record created for it. A self-registered person's record starts
empty; a clinic patient's record is created by their care manager, who records what the
clinic established. Nothing here ever copies, claims or renames a synthetic record.
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit
from app.clinical import activity
from app.core import clock
from app.core.permissions import Permission as P
from app.core.permissions import can
from app.models import (
    CareManagerPatient,
    CareNote,
    CareRequest,
    Consent,
    Hcp,
    MedicationFill,
    Patient,
    PatientCondition,
    PatientHcp,
    PatientNumber,
    PatientTherapy,
    User,
)
from app.models.enums import AccountSource, AccountStatus, PatientOrigin
from app.models.tables import utcnow

REAL = (PatientOrigin.SELF_REGISTERED, PatientOrigin.CLINIC)


def is_real(patient: Patient | None) -> bool:
    return patient is not None and patient.origin != PatientOrigin.SYNTHETIC


def split_name(name: str) -> tuple[str, str]:
    first, _, rest = " ".join(name.split()).partition(" ")
    return first[:64], rest.strip()[:64]


def new_patient_id(db: Session) -> str:
    """PAT_R000001, PAT_R000002, ... from a counter that never goes back."""
    number = PatientNumber(created_at=utcnow())
    db.add(number)
    db.flush()
    return f"PAT_R{number.id:06d}"


def create_patient(
    db: Session,
    *,
    name: str,
    birth_date: date,
    country: str | None,
    region: str | None,
    origin: str,
    created_by: User | None = None,
) -> Patient:
    """A new, empty record: no medications, refills, conditions, consent or history."""
    first, last = split_name(name)
    patient = Patient(
        patient_id=new_patient_id(db),
        first_name=first,
        last_name=last,
        birth_date=birth_date,
        country=country,
        region=region,
        origin=origin,
        created_by_user_id=created_by.id if created_by else None,
    )
    db.add(patient)
    db.flush()
    return patient


def care_managers(db: Session) -> list[User]:
    """Active accounts that manage patient care (by permission, never by role name)."""
    candidates = db.scalars(
        select(User).where(User.status == AccountStatus.ACTIVE).order_by(User.id)
    ).all()
    return [u for u in candidates if can(u, P.PATIENT_CARE_MANAGE)]


def _real_loads(db: Session) -> dict[int, int]:
    return dict(
        db.execute(
            select(CareManagerPatient.care_manager_user_id, func.count())
            .join(Patient, Patient.patient_id == CareManagerPatient.patient_id)
            .where(Patient.origin.in_(REAL))
            .group_by(CareManagerPatient.care_manager_user_id)
        ).all()
    )


def pick_care_manager(db: Session, *, excluding: int | None = None) -> User | None:
    """The active care manager with the fewest real patients; ties go to the earliest
    account. Deterministic, and it creates no clinical data."""
    managers = [u for u in care_managers(db) if u.id != excluding]
    if not managers:
        return None
    loads = _real_loads(db)
    return min(managers, key=lambda u: (loads.get(u.id, 0), u.id))


def assign_care_manager(db: Session, patient: Patient, manager: User | None) -> None:
    """Makes `manager` the care manager responsible for the patient. A real patient has
    exactly one: any other link is moved, with the open work it owns."""
    if manager is None:
        return
    if is_real(patient):
        for link in db.scalars(
            select(CareManagerPatient).where(
                CareManagerPatient.patient_id == patient.patient_id,
                CareManagerPatient.care_manager_user_id != manager.id,
            )
        ).all():
            _move_owned_work(db, patient.patient_id, link.care_manager_user_id, manager.id)
            db.delete(link)
    exists = db.get(CareManagerPatient, (manager.id, patient.patient_id))
    if exists is None:
        db.add(CareManagerPatient(care_manager_user_id=manager.id, patient_id=patient.patient_id))
    db.flush()


def _move_owned_work(db: Session, patient_id: str, from_id: int, to_id: int) -> None:
    """Follow-ups the previous care manager owned for this patient move with the patient."""
    for r in db.scalars(
        select(CareRequest).where(
            CareRequest.patient_id == patient_id,
            CareRequest.owner_user_id == from_id,
            CareRequest.status != "closed",
        )
    ):
        r.owner_user_id = to_id


def responsible_care_managers(
    db: Session, patient_id: str, *, active_only: bool = False
) -> list[User]:
    ids = db.scalars(
        select(CareManagerPatient.care_manager_user_id).where(
            CareManagerPatient.patient_id == patient_id
        )
    ).all()
    users = [u for u in (db.get(User, i) for i in ids) if u is not None]
    if active_only:
        users = [u for u in users if u.status == AccountStatus.ACTIVE]
    return users


def real_patients_of(db: Session, manager: User) -> list[str]:
    return list(
        db.scalars(
            select(CareManagerPatient.patient_id)
            .join(Patient, Patient.patient_id == CareManagerPatient.patient_id)
            .where(
                CareManagerPatient.care_manager_user_id == manager.id,
                Patient.origin.in_(REAL),
            )
        )
    )


def unassigned_real_patients(db: Session) -> list[str]:
    """Real patients with no active care manager: nobody would see their requests."""
    active = (
        select(CareManagerPatient.patient_id)
        .join(User, User.id == CareManagerPatient.care_manager_user_id)
        .where(User.status == AccountStatus.ACTIVE)
    )
    return list(
        db.scalars(
            select(Patient.patient_id)
            .where(Patient.origin.in_(REAL), Patient.patient_id.not_in(active))
            .order_by(Patient.patient_id)
        )
    )


def transfer_impact(db: Session, manager: User) -> dict:
    """What disabling this care manager would move, and to whom."""
    from app.models.enums import CareRequestStatus

    patients = real_patients_of(db, manager)
    open_requests = (
        db.scalar(
            select(func.count())
            .select_from(CareRequest)
            .where(
                CareRequest.patient_id.in_(patients),
                CareRequest.status != CareRequestStatus.CLOSED,
            )
        )
        if patients
        else 0
    )
    target = pick_care_manager(db, excluding=manager.id)
    return {
        "real_patients": len(patients),
        "open_requests": open_requests or 0,
        "to": {"id": target.id, "name": target.display_name} if target else None,
    }


def transfer_patients(db: Session, manager: User, actor: User) -> int:
    """The care manager can no longer act (disabled): each of their real patients moves to the
    least-loaded active care manager, with their open work. Synthetic panels are unaffected."""
    db.flush()
    moved = 0
    for pid in real_patients_of(db, manager):
        target = pick_care_manager(db, excluding=manager.id)
        if target is None:
            break
        assign_care_manager(db, db.get(Patient, pid), target)
        audit.record(
            db, "care_manager_reassigned", "patient", pid, actor=actor.username,
            actor_role=actor.role, detail={"from": manager.id, "to": target.id},
        )  # fmt: skip
        moved += 1
    return moved


def adopt_unassigned(db: Session, actor: str = "system", actor_role: str = "system") -> int:
    """Real patients waiting for a care manager get one as soon as one is active."""
    db.flush()  # account status changes made in this request must count
    adopted = 0
    for pid in unassigned_real_patients(db):
        target = pick_care_manager(db)
        if target is None:
            break
        assign_care_manager(db, db.get(Patient, pid), target)
        audit.record(
            db, "care_manager_assigned", "patient", pid, actor=actor, actor_role=actor_role,
            detail={"to": target.id},
        )  # fmt: skip
        adopted += 1
    return adopted


def provision_self_registered(db: Session, user: User) -> Patient:
    """Path A: a person who registered themselves gets their own empty record and a care
    manager. Called once, when the account is verified."""
    patient = create_patient(
        db,
        name=user.display_name,
        birth_date=user.date_of_birth or clock.today(),
        country=user.country,
        region=user.region,
        origin=PatientOrigin.SELF_REGISTERED,
    )
    user.patient_id = patient.patient_id
    manager = pick_care_manager(db)
    assign_care_manager(db, patient, manager)
    audit.record(
        db, "patient_record_created", "patient", patient.patient_id, actor=user.username,
        actor_role=user.role,
        detail={"origin": patient.origin, "care_manager": manager.id if manager else None},
    )  # fmt: skip
    return patient


def sync_location(db: Session, user: User) -> None:
    """A real patient's record follows the country the account holder gave."""
    patient = db.get(Patient, user.patient_id) if user.patient_id else None
    if is_real(patient):
        patient.country, patient.region = user.country, user.region
        activity.touch(db, patient.patient_id)


# --- Accounts registered before records were separated ---------------------------------


def separate_real_patients(db: Session) -> int:
    """Accounts that registered (or were invited) themselves but are linked to a synthetic
    record get their own empty record; the synthetic record gets its generated name back.
    Idempotent; run at start-up. Returns how many accounts were moved."""
    from app.datagen.generate import synthetic_patient_names

    moved = [
        user
        for user in db.scalars(
            select(User).where(
                User.patient_id.is_not(None),
                User.source.in_((AccountSource.SIGNUP, AccountSource.INVITATION)),
            )
        )
        if not is_real(db.get(Patient, user.patient_id))
    ]
    if not moved:
        return 0
    old_ids = [u.patient_id for u in moved]
    names = synthetic_patient_names(db.scalar(select(func.count()).select_from(Hcp)), old_ids)
    for user in moved:
        old = db.get(Patient, user.patient_id)
        if old.patient_id in names:
            old.first_name, old.last_name = names[old.patient_id]
        user.patient_id = None
        db.flush()
        patient = provision_self_registered(db, user)
        audit.record(
            db, "patient_record_separated", "user", user.id,
            detail={"previous_record": old.patient_id, "new_record": patient.patient_id},
        )  # fmt: skip
    db.flush()
    return len(moved)


# --- Surviving a demo reset ------------------------------------------------------------


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


def snapshot(db: Session) -> dict:
    """Every real patient with what was recorded for them. Account references are saved by
    email, because account ids change in a rebuild."""
    emails = dict(db.execute(select(User.id, User.email)).all())
    ids = list(db.scalars(select(Patient.patient_id).where(Patient.origin.in_(REAL))))
    if not ids:
        return {}

    def rows(model, *, users=()):
        out = []
        for r in db.scalars(select(model).where(model.patient_id.in_(ids))):
            row = _row(r)
            out.append({"row": row, "users": {c: emails.get(row[c]) for c in users}})
        return out

    return {
        "patients": [
            {"row": _row(p), "users": {"created_by_user_id": emails.get(p.created_by_user_id)}}
            for p in db.scalars(select(Patient).where(Patient.patient_id.in_(ids)))
        ],
        "conditions": rows(PatientCondition, users=("confirmed_by_user_id",)),
        "therapies": rows(PatientTherapy, users=("confirmed_by_user_id",)),
        "fills": rows(MedicationFill),
        "consents": rows(Consent),
        "requests": rows(CareRequest, users=("handled_by_user_id",)),
        "notes": rows(CareNote, users=("author_user_id",)),
        "hcps": rows(PatientHcp),
        "care_managers": [
            {"patient_id": pid, "email": emails.get(uid)}
            for uid, pid in db.execute(
                select(
                    CareManagerPatient.care_manager_user_id, CareManagerPatient.patient_id
                ).where(CareManagerPatient.patient_id.in_(ids))
            )
        ],
    }


def restore(db: Session, saved: dict) -> None:
    """Re-creates real patients and their records after the rebuild, before accounts are
    restored (accounts point at their records). Account references are filled in by
    `restore_links` once the accounts exist again."""
    if not saved:
        return
    hcps = set(db.scalars(select(Hcp.hcp_id)))
    for item in saved["patients"]:
        db.add(Patient(**(item["row"] | {"created_by_user_id": None})))
    db.flush()
    conditions, therapies = {}, {}
    for item in saved["conditions"]:
        row = dict(item["row"])
        old = row.pop("id")
        c = PatientCondition(**(row | {"confirmed_by_user_id": None}))
        db.add(c)
        conditions[old] = c
    for item in saved["therapies"]:
        row = dict(item["row"])
        old = row.pop("id")
        if row["prescriber_hcp_id"] not in hcps:
            row["prescriber_hcp_id"] = None
        t = PatientTherapy(**(row | {"confirmed_by_user_id": None}))
        db.add(t)
        therapies[old] = t
    db.flush()
    for item in saved["fills"]:
        row = dict(item["row"])
        row.pop("id")
        if row["therapy_id"] in therapies:
            db.add(MedicationFill(**(row | {"therapy_id": therapies[row["therapy_id"]].id})))
    for item in saved["consents"]:
        row = dict(item["row"])
        row.pop("id")
        db.add(Consent(**row))
    for item in saved["requests"]:
        row = dict(item["row"])
        row.pop("id")
        row["condition_id"] = (
            conditions[row["condition_id"]].id if row["condition_id"] in conditions else None
        )
        row["therapy_id"] = (
            therapies[row["therapy_id"]].id if row["therapy_id"] in therapies else None
        )
        if row["assigned_hcp_id"] not in hcps:
            row["assigned_hcp_id"] = None
        db.add(CareRequest(**(row | {"handled_by_user_id": None})))
    for item in saved["notes"]:
        row = dict(item["row"])
        row.pop("id")
        if row["hcp_id"] not in hcps:
            row["hcp_id"] = None
        db.add(CareNote(**(row | {"author_user_id": None})))
    for item in saved["hcps"]:
        if item["row"]["hcp_id"] in hcps:
            db.add(PatientHcp(**item["row"]))
    db.flush()
    # Remember the new rows so restore_links can set account references on them.
    saved["_new"] = {"conditions": conditions, "therapies": therapies}


def restore_links(db: Session, saved: dict) -> None:
    """Account references on real patients' records, re-linked by email."""
    if not saved:
        return
    ids = dict(db.execute(select(User.email, User.id)).all())
    for item in saved["patients"]:
        email = item["users"]["created_by_user_id"]
        if email in ids:
            db.get(Patient, item["row"]["patient_id"]).created_by_user_id = ids[email]
    new = saved.get("_new", {})
    for key in ("conditions", "therapies"):
        for item in saved[key]:
            email = item["users"]["confirmed_by_user_id"]
            obj = new.get(key, {}).get(item["row"]["id"])
            if obj is not None and email in ids:
                obj.confirmed_by_user_id = ids[email]
    for model, key, column in (
        (CareRequest, "requests", "handled_by_user_id"),
        (CareNote, "notes", "author_user_id"),
    ):
        for item in saved[key]:
            email = item["users"][column]
            if email not in ids:
                continue
            match = db.scalar(
                select(model).where(
                    model.patient_id == item["row"]["patient_id"],
                    model.created_at == item["row"]["created_at"],
                )
            )
            if match is not None:
                setattr(match, column, ids[email])
    for item in saved["care_managers"]:
        if item["email"] in ids:
            manager = db.get(User, ids[item["email"]])
            assign_care_manager(db, db.get(Patient, item["patient_id"]), manager)
    db.flush()
