"""Assignments: which synthetic records an account is linked to.

Assignments decide data scope only. Nothing here reads or writes `user.role`, so changing
what a user is assigned to can never change what they are permitted to do.

The shape of an assignment follows from the account's permissions:

  PATIENT_READ_ASSIGNED   a panel of patients     (care_manager_patient rows)
  HCP_READ_ASSIGNED       a set of HCPs           (rep_hcp rows)
  SELF_CONSENT_MANAGE     one own patient record  (user.patient_id)
  SELF_PATIENTS_READ      one own HCP record      (user.hcp_id)
"""

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.errors import AuthError
from app.core.config import get_settings
from app.core.permissions import Permission as P
from app.core.permissions import can
from app.models import CareManagerPatient, Hcp, Nba, Patient, RepHcp, User
from app.models.enums import AccountSource, NbaStatus, RiskSegment, TargetType

PATIENTS, HCPS, OWN_PATIENT, OWN_HCP = "patients", "hcps", "patient", "hcp"


def assignment_kind(user: User) -> str | None:
    if can(user, P.PATIENT_READ_ASSIGNED):
        return PATIENTS
    if can(user, P.HCP_READ_ASSIGNED):
        return HCPS
    if can(user, P.SELF_CONSENT_MANAGE):
        return OWN_PATIENT
    if can(user, P.SELF_PATIENTS_READ):
        return OWN_HCP
    return None


def _adopt_name(record: Patient | Hcp, user: User) -> None:
    """The claimed synthetic record is shown under the account holder's name."""
    first, _, rest = user.display_name.strip().partition(" ")
    record.first_name, record.last_name = first[:64], rest.strip()[:64]


def _spread(ids: list[str], size: int, offset: int) -> list[str]:
    """`size` ids taken evenly across the list, shifted so successive accounts differ."""
    if len(ids) <= size:
        return ids
    step = len(ids) // size
    return ids[offset % step :: step][:size]


def _ready_targets(target_type: str):
    return select(Nba.target_id).where(
        Nba.target_type == target_type, Nba.status == NbaStatus.READY_FOR_REVIEW
    )


def _peers(db: Session, user: User) -> int:
    """How many other registered accounts share this role (used to vary what each gets)."""
    return db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == user.role, User.source == AccountSource.SIGNUP, User.id != user.id)
    )


class AssignmentService:
    # --- reading ---------------------------------------------------------------

    def patient_ids(self, db: Session, user: User) -> list[str]:
        return list(
            db.scalars(
                select(CareManagerPatient.patient_id)
                .where(CareManagerPatient.care_manager_user_id == user.id)
                .order_by(CareManagerPatient.patient_id)
            )
        )

    def hcp_ids(self, db: Session, user: User) -> list[str]:
        return list(
            db.scalars(
                select(RepHcp.hcp_id).where(RepHcp.rep_user_id == user.id).order_by(RepHcp.hcp_id)
            )
        )

    def summary(self, db: Session, user: User) -> dict:
        kind = assignment_kind(user)
        if kind == PATIENTS:
            count = db.scalar(
                select(func.count())
                .select_from(CareManagerPatient)
                .where(CareManagerPatient.care_manager_user_id == user.id)
            )
            return {"kind": kind, "count": count}
        if kind == HCPS:
            count = db.scalar(
                select(func.count()).select_from(RepHcp).where(RepHcp.rep_user_id == user.id)
            )
            return {"kind": kind, "count": count}
        if kind == OWN_PATIENT:
            return {"kind": kind, "count": int(user.patient_id is not None), "id": user.patient_id}
        if kind == OWN_HCP:
            return {"kind": kind, "count": int(user.hcp_id is not None), "id": user.hcp_id}
        return {"kind": None, "count": 0}

    # --- automatic provisioning on verification --------------------------------

    def auto_provision(self, db: Session, user: User) -> dict:
        """Gives a newly verified account its starting data. Existing rows are not touched."""
        settings = get_settings()
        kind = assignment_kind(user)
        if kind == OWN_PATIENT and user.patient_id is None:
            linked = select(User.patient_id).where(User.patient_id.is_not(None))
            ready = _ready_targets(TargetType.PATIENT)
            record = db.scalar(
                select(Patient)
                .where(Patient.patient_id.not_in(linked))
                .order_by(Patient.patient_id.in_(ready).desc(), Patient.patient_id)
            )
            if record:
                user.patient_id = record.patient_id
                _adopt_name(record, user)
        elif kind == OWN_HCP and user.hcp_id is None:
            linked = select(User.hcp_id).where(User.hcp_id.is_not(None))
            ready = _ready_targets(TargetType.HCP)
            record = db.scalar(
                select(Hcp)
                .where(Hcp.hcp_id.not_in(linked))
                .order_by(Hcp.hcp_id.in_(ready).desc(), Hcp.hcp_id)
            )
            if record:
                user.hcp_id = record.hcp_id
                _adopt_name(record, user)
        elif kind == PATIENTS and not self.patient_ids(db, user):
            offset = _peers(db, user)
            ready = _ready_targets(TargetType.PATIENT)
            per_segment, extra = divmod(settings.signup_panel_patients, 3)
            chosen: list[str] = []
            for n, segment in enumerate((RiskSegment.HIGH, RiskSegment.MEDIUM, RiskSegment.LOW)):
                ids = list(
                    db.scalars(
                        select(Patient.patient_id)
                        .where(Patient.risk_segment == segment)
                        .order_by(Patient.patient_id.in_(ready).desc(), Patient.patient_id)
                    )
                )
                chosen += _spread(ids, per_segment + (extra if n == 0 else 0), offset)
            if not chosen:  # features not calculated yet: no risk segments to spread across
                ids = list(db.scalars(select(Patient.patient_id).order_by(Patient.patient_id)))
                chosen = _spread(ids, settings.signup_panel_patients, offset)
            db.add_all(
                CareManagerPatient(care_manager_user_id=user.id, patient_id=pid) for pid in chosen
            )
        elif kind == HCPS and not self.hcp_ids(db, user):
            ids = list(
                db.scalars(
                    select(Hcp.hcp_id).order_by(Hcp.value_score.desc().nulls_last(), Hcp.hcp_id)
                )
            )
            chosen = _spread(ids, settings.signup_panel_hcps, _peers(db, user))
            db.add_all(RepHcp(rep_user_id=user.id, hcp_id=hid) for hid in chosen)
        db.flush()
        return self.summary(db, user)

    # --- administrator edits ---------------------------------------------------

    def replace(
        self,
        db: Session,
        user: User,
        *,
        patient_ids: list[str] | None = None,
        hcp_ids: list[str] | None = None,
        patient_id: str | None = None,
        hcp_id: str | None = None,
        fields_set: set[str],
    ) -> dict:
        """Replaces the account's assignments. The fields sent must match the account's kind."""
        kind = assignment_kind(user)
        expected = {
            PATIENTS: "patient_ids",
            HCPS: "hcp_ids",
            OWN_PATIENT: "patient_id",
            OWN_HCP: "hcp_id",
        }
        if kind is None:
            raise AuthError(409, "no_assignments_for_role", "This role has no assignments.")
        if fields_set != {expected[kind]}:
            raise AuthError(
                422, "invalid_assignment", f"Send only '{expected[kind]}' for this account."
            )

        if kind == PATIENTS:
            wanted = sorted(set(patient_ids or []))
            self._require_existing(db, Patient.patient_id, wanted, "patient")
            db.execute(
                delete(CareManagerPatient).where(CareManagerPatient.care_manager_user_id == user.id)
            )
            db.add_all(
                CareManagerPatient(care_manager_user_id=user.id, patient_id=p) for p in wanted
            )
        elif kind == HCPS:
            wanted = sorted(set(hcp_ids or []))
            self._require_existing(db, Hcp.hcp_id, wanted, "HCP")
            db.execute(delete(RepHcp).where(RepHcp.rep_user_id == user.id))
            db.add_all(RepHcp(rep_user_id=user.id, hcp_id=h) for h in wanted)
        elif kind == OWN_PATIENT:
            if patient_id is not None:
                self._require_existing(db, Patient.patient_id, [patient_id], "patient")
                self._require_unlinked(db, User.patient_id, patient_id, user)
                if patient_id != user.patient_id:
                    _adopt_name(db.get(Patient, patient_id), user)
            user.patient_id = patient_id
        else:
            if hcp_id is not None:
                self._require_existing(db, Hcp.hcp_id, [hcp_id], "HCP")
                self._require_unlinked(db, User.hcp_id, hcp_id, user)
                if hcp_id != user.hcp_id:
                    _adopt_name(db.get(Hcp, hcp_id), user)
            user.hcp_id = hcp_id
        db.flush()
        return self.summary(db, user)

    @staticmethod
    def _require_existing(db: Session, column, ids: list[str], label: str) -> None:
        found = set(db.scalars(select(column).where(column.in_(ids)))) if ids else set()
        missing = sorted(set(ids) - found)
        if missing:
            raise AuthError(422, "unknown_record", f"Unknown {label} id: {', '.join(missing[:5])}")

    @staticmethod
    def _require_unlinked(db: Session, column, value: str, user: User) -> None:
        other = db.scalar(select(User).where(column == value, User.id != user.id))
        if other:
            raise AuthError(409, "record_in_use", "That record is linked to another account.")

    # --- surviving a demo reset --------------------------------------------------

    def snapshot(self, db: Session) -> list[dict]:
        """Registered accounts and what they were assigned, taken before the data is rebuilt."""
        saved = []
        for user in db.scalars(select(User).where(User.source == AccountSource.SIGNUP)):
            saved.append(
                {
                    "account": {
                        c.name: getattr(user, c.name)
                        for c in User.__table__.columns
                        if c.name != "id"
                    },
                    "patient_ids": self.patient_ids(db, user),
                    "hcp_ids": self.hcp_ids(db, user),
                }
            )
        return saved

    def restore(self, db: Session, saved: list[dict]) -> None:
        """Re-creates registered accounts after a rebuild. Synthetic ids are deterministic, so
        earlier assignments are re-linked where the record still exists at the new scale."""
        patients = set(db.scalars(select(Patient.patient_id)))
        hcps = set(db.scalars(select(Hcp.hcp_id)))
        for item in saved:
            account = dict(item["account"])
            if account["patient_id"] not in patients:
                account["patient_id"] = None
            if account["hcp_id"] not in hcps:
                account["hcp_id"] = None
            user = User(**account)
            db.add(user)
            db.flush()
            if user.patient_id:
                _adopt_name(db.get(Patient, user.patient_id), user)
            if user.hcp_id:
                _adopt_name(db.get(Hcp, user.hcp_id), user)
            db.add_all(
                CareManagerPatient(care_manager_user_id=user.id, patient_id=p)
                for p in item["patient_ids"]
                if p in patients
            )
            db.add_all(RepHcp(rep_user_id=user.id, hcp_id=h) for h in item["hcp_ids"] if h in hcps)
        db.flush()


assignments = AssignmentService()
