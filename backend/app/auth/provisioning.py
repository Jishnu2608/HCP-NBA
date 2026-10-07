"""Assignments: which records an account is linked to.

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
from app.clinical import activity, hcps, records
from app.core.config import get_settings
from app.core.permissions import Permission as P
from app.core.permissions import can
from app.models import (
    AuditLog,
    CareManagerPatient,
    ConsentRecord,
    Hcp,
    Invitation,
    Nba,
    Patient,
    PrivacyRequest,
    RepHcp,
    User,
)
from app.models.enums import AccountSource, NbaStatus, PatientOrigin, RiskSegment, TargetType

SYNTHETIC = PatientOrigin.SYNTHETIC

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
    """How many earlier accounts share this role (used to vary what each gets): invited,
    registered and seeded alike, so successive staff accounts start on different records."""
    return db.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.role == user.role,
            User.source.in_((AccountSource.SIGNUP, AccountSource.INVITATION, AccountSource.SEED)),
            User.id < user.id,
        )
    )


class AssignmentService:
    # --- reading ---------------------------------------------------------------

    def patient_ids(self, db: Session, user: User) -> list[str]:
        return list(
            db.scalars(
                select(CareManagerPatient.patient_id)
                .join(Patient, Patient.patient_id == CareManagerPatient.patient_id)
                .where(CareManagerPatient.care_manager_user_id == user.id)
                .order_by(*activity.patient_order())
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
            # A real person gets their own empty record, never a synthetic one. (A clinic
            # patient's record is linked from the invitation before this runs.)
            records.provision_self_registered(db, user)
        elif kind == OWN_HCP and user.hcp_id is None:
            # A real HCP gets a blank record, never a synthetic one, and no specialty unless
            # one was set (an invitation's specialties are applied before this runs).
            user.hcp_id = hcps.create_hcp(db, user.display_name, []).hcp_id
        elif kind == PATIENTS and not self.patient_ids(db, user):
            offset = _peers(db, user)
            ready = _ready_targets(TargetType.PATIENT)
            per_segment, extra = divmod(settings.signup_panel_patients, 3)
            chosen: list[str] = []
            for n, segment in enumerate((RiskSegment.HIGH, RiskSegment.MEDIUM, RiskSegment.LOW)):
                ids = list(
                    db.scalars(
                        select(Patient.patient_id)
                        .where(Patient.risk_segment == segment, Patient.origin == SYNTHETIC)
                        .order_by(Patient.patient_id.in_(ready).desc(), Patient.patient_id)
                    )
                )
                chosen += _spread(ids, per_segment + (extra if n == 0 else 0), offset)
            if not chosen:  # features not calculated yet: no risk segments to spread across
                ids = list(
                    db.scalars(
                        select(Patient.patient_id)
                        .where(Patient.origin == SYNTHETIC)
                        .order_by(Patient.patient_id)
                    )
                )
                chosen = _spread(ids, settings.signup_panel_patients, offset)
            db.add_all(
                CareManagerPatient(care_manager_user_id=user.id, patient_id=pid) for pid in chosen
            )
            db.flush()
            # Real patients who were waiting for a care manager get one now.
            records.adopt_unassigned(db)
        elif kind == HCPS and not self.hcp_ids(db, user):
            ids = list(
                db.scalars(
                    select(Hcp.hcp_id)
                    .where(Hcp.origin == hcps.SYNTHETIC)
                    .order_by(Hcp.value_score.desc().nulls_last(), Hcp.hcp_id)
                )
            )
            chosen = _spread(ids, settings.signup_panel_hcps, _peers(db, user))
            inviter = db.get(User, user.invited_by_user_id) if user.invited_by_user_id else None
            if inviter is not None and inviter.hcp_id and inviter.hcp_id not in chosen:
                # Invited by an HCP: the representative works with that HCP.
                chosen = [inviter.hcp_id, *chosen]
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
        actor: User | None = None,
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
            # A real patient always has exactly one responsible care manager: removing one here
            # would leave them with nobody, so they are moved by assigning them elsewhere.
            dropped = [pid for pid in records.real_patients_of(db, user) if pid not in set(wanted)]
            if dropped:
                raise AuthError(
                    409,
                    "would_orphan",
                    "These real patients would be left without a care manager. Assign them to "
                    "another care manager first.",
                    patients=dropped,
                )
            db.execute(
                delete(CareManagerPatient).where(
                    CareManagerPatient.care_manager_user_id == user.id,
                    CareManagerPatient.patient_id.not_in(
                        select(Patient.patient_id).where(Patient.origin.in_(records.REAL))
                    ),
                )
            )
            for pid in wanted:
                patient = db.get(Patient, pid)
                if records.is_real(patient):
                    # Moves the patient (and their open follow-ups) from any other care manager.
                    records.assign_care_manager(
                        db, patient, user,
                        actor=actor.username if actor else "system",
                        actor_role=actor.role if actor else "system",
                    )  # fmt: skip
                else:
                    db.add(CareManagerPatient(care_manager_user_id=user.id, patient_id=pid))
        elif kind == HCPS:
            wanted = sorted(set(hcp_ids or []))
            self._require_existing(db, Hcp.hcp_id, wanted, "HCP")
            before = list(db.scalars(select(RepHcp.hcp_id).where(RepHcp.rep_user_id == user.id)))
            db.execute(delete(RepHcp).where(RepHcp.rep_user_id == user.id))
            db.add_all(RepHcp(rep_user_id=user.id, hcp_id=h) for h in wanted)
            db.flush()
            # Open HCP requests, follow-ups and meetings follow the assignment.
            from app.commercial import tasks as hcp_tasks

            hcp_tasks.reassign_open_work(db, sorted(set(before) | set(wanted)))
        elif kind == OWN_PATIENT:
            if patient_id is not None:
                self._require_existing(db, Patient.patient_id, [patient_id], "patient")
                self._require_unlinked(db, User.patient_id, patient_id, user)
                # A demo account may only use demo records; a real person only a real
                # record. A real person's data must never come from the synthetic population.
                seeded = user.source == AccountSource.SEED
                if records.is_real(db.get(Patient, patient_id)) == seeded:
                    raise AuthError(
                        409,
                        "record_origin_mismatch",
                        "Demo accounts use demo records and real accounts their own record.",
                    )
                if patient_id != user.patient_id and seeded:
                    _adopt_name(db.get(Patient, patient_id), user)
            user.patient_id = patient_id
        else:
            if hcp_id is not None:
                self._require_existing(db, Hcp.hcp_id, [hcp_id], "HCP")
                self._require_unlinked(db, User.hcp_id, hcp_id, user)
                seeded = user.source == AccountSource.SEED
                if hcps.is_real(db.get(Hcp, hcp_id)) == seeded:
                    raise AuthError(
                        409,
                        "record_origin_mismatch",
                        "Demo accounts use demo records and real accounts their own record.",
                    )
                if hcp_id != user.hcp_id and seeded:
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
    #
    # A reset rebuilds the demo data. What is not demo data is carried across: registered
    # and invited accounts with their assignments, invitations (with lineage), and the
    # security part of the audit trail. Account ids can change in a rebuild, so every
    # reference between accounts is saved by email and re-linked afterwards, and all
    # sessions end (sessions.revoke_everyone) so no old cookie can land on a new id.

    KEPT_SOURCES = (AccountSource.SIGNUP, AccountSource.INVITATION)
    # Account and security events, and content governance (MLR decisions, submissions).
    SECURITY_AUDIT = ("user", "auth", "invitation", "content")

    def snapshot(self, db: Session) -> dict:
        emails = dict(db.execute(select(User.id, User.email)).all())
        accounts = []
        for user in db.scalars(select(User).where(User.source.in_(self.KEPT_SOURCES))):
            account = {c.name: getattr(user, c.name) for c in User.__table__.columns}
            account.pop("id")
            account["invited_by_user_id"] = None
            accounts.append(
                {
                    "account": account,
                    "invited_by": emails.get(user.invited_by_user_id),
                    "patient_ids": self.patient_ids(db, user),
                    "hcp_ids": self.hcp_ids(db, user),
                }
            )
        invites = []
        for inv in db.scalars(select(Invitation).order_by(Invitation.id)):
            row = {c.name: getattr(inv, c.name) for c in Invitation.__table__.columns}
            invites.append(
                {
                    "row": row,
                    "invited_by": emails.get(inv.invited_by_user_id),
                    "revoked_by": emails.get(inv.revoked_by_user_id),
                    "claimed": emails.get(inv.claimed_user_id),
                }
            )
        audit_rows = [
            {
                **{
                    c.name: getattr(a, c.name) for c in AuditLog.__table__.columns if c.name != "id"
                },
                # Account ids change in a rebuild: the acting account is re-linked by email.
                "actor_user_id": emails.get(a.actor_user_id),
            }
            for a in db.scalars(
                select(AuditLog)
                .where(AuditLog.entity_type.in_(self.SECURITY_AUDIT), AuditLog.nba_id.is_(None))
                .order_by(AuditLog.id)
            )
        ]

        # Consent records and privacy requests belong to the person, not to the demo data:
        # kept for every account that exists again after the rebuild (matched by email).
        def by_email(model, *user_columns):
            rows = []
            for r in db.scalars(select(model).order_by(model.id)):
                row = {c.name: getattr(r, c.name) for c in model.__table__.columns}
                row.pop("id")
                rows.append({"row": row, "emails": {c: emails.get(row[c]) for c in user_columns}})
            return rows

        return {
            "accounts": accounts,
            "invitations": invites,
            "audit": audit_rows,
            "consents": by_email(ConsentRecord, "user_id"),
            "privacy_requests": by_email(PrivacyRequest, "user_id", "handled_by_user_id"),
        }

    def restore(self, db: Session, saved: dict) -> None:
        """Re-creates kept accounts, invitations and security audit rows after a rebuild.
        Synthetic ids are deterministic, so earlier assignments are re-linked where the
        record still exists at the new scale."""
        patients = set(db.scalars(select(Patient.patient_id)))
        hcps = set(db.scalars(select(Hcp.hcp_id)))
        for item in saved.get("accounts", []):
            account = dict(item["account"])
            if account["patient_id"] not in patients:
                account["patient_id"] = None
            if account["hcp_id"] not in hcps:
                account["hcp_id"] = None
            user = User(**account)
            db.add(user)
            db.flush()
            if user.hcp_id:
                _adopt_name(db.get(Hcp, user.hcp_id), user)
            db.add_all(
                CareManagerPatient(care_manager_user_id=user.id, patient_id=p)
                for p in item["patient_ids"]
                if p in patients
            )
            db.add_all(RepHcp(rep_user_id=user.id, hcp_id=h) for h in item["hcp_ids"] if h in hcps)
        db.flush()

        ids = dict(db.execute(select(User.email, User.id)).all())
        db.add_all(
            AuditLog(**{**row, "actor_user_id": ids.get(row["actor_user_id"])})
            for row in saved.get("audit", [])
        )
        for item in saved.get("accounts", []):
            if item["invited_by"] in ids:
                user = db.get(User, ids[item["account"]["email"]])
                user.invited_by_user_id = ids[item["invited_by"]]
        new_ids: dict[int, Invitation] = {}
        for item in saved.get("invitations", []):
            if item["invited_by"] not in ids:
                continue  # the inviter no longer exists at this scale
            row = dict(item["row"])
            old_id = row.pop("id")
            row.update(
                invited_by_user_id=ids[item["invited_by"]],
                revoked_by_user_id=ids.get(item["revoked_by"]),
                claimed_user_id=ids.get(item["claimed"]),
                replaced_by_id=None,
            )
            inv = Invitation(**row)
            db.add(inv)
            new_ids[old_id] = inv
        db.flush()
        for item in saved.get("invitations", []):
            old = item["row"]
            if old["id"] in new_ids and old["replaced_by_id"] in new_ids:
                new_ids[old["id"]].replaced_by_id = new_ids[old["replaced_by_id"]].id
        for model, key in ((ConsentRecord, "consents"), (PrivacyRequest, "privacy_requests")):
            for item in saved.get(key, []):
                if item["emails"]["user_id"] not in ids:
                    continue
                row = dict(item["row"])
                for column, email in item["emails"].items():
                    row[column] = ids.get(email)
                db.add(model(**row))
        db.flush()


assignments = AssignmentService()
