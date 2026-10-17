"""Role-based patient PII masking, decided on the server for every response.

Who sees a patient's PII unmasked (owner decision 2026-10-18), by permission and assignment,
never by role name, and never because a permission grants access to every record:

- the patient themselves (`user.patient_id`);
- a care manager (`patient:read:assigned`) with a care-manager assignment to the patient;
- an HCP (`self:patients:read`) on the patient's care team, or with one of the patient's
  consultations currently routed to them.

Everyone else sees masked values, including the administrator (`patient:read:all` lets a role
open a record, not see who it is), representatives and compliance. No override exists; an
approved access policy would be a reviewed code change here.

Masked forms:
- name: each whitespace-separated part keeps its first character, followed by length - 1
  asterisks (`Gayatri Saikia` -> `G****** S*****`);
- email: the local part and every domain label but the last masked the same way
  (`pat00001@nba.demo` -> `p*******@n**.demo`);
- birth date: withheld (age only); ZIP: withheld;
- free text: the patient's own first and last name masked wherever they appear.
"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.permissions import Permission as P
from app.core.permissions import can
from app.models import CareManagerPatient, CareRequest, Patient, PatientHcp, User

MASK = "*"


def mask_word(word: str) -> str:
    return word[:1] + MASK * (len(word) - 1) if word else word


def mask_name(name: str | None) -> str | None:
    """`Gayatri Saikia` -> `G****** S*****`. Whitespace is kept as it is."""
    if name is None:
        return None
    return re.sub(r"\S+", lambda m: mask_word(m.group()), name)


def mask_email(email: str | None) -> str | None:
    """`pat00001@nba.demo` -> `p*******@n**.demo`."""
    if not email or "@" not in email:
        return mask_name(email)
    local, _, domain = email.partition("@")
    labels = domain.split(".")
    masked = [mask_word(x) for x in labels[:-1]] + labels[-1:]
    return f"{mask_word(local)}@{'.'.join(masked)}"


def mask_in_text(text: str | None, names: list[str]) -> str | None:
    """Masks each given name part (three letters or more, whole words, any case) in text."""
    if not text:
        return text
    parts = sorted({p for n in names if n for p in n.split() if len(p) >= 3}, key=len, reverse=True)
    for part in parts:
        text = re.sub(rf"(?<!\w){re.escape(part)}(?!\w)", lambda m: mask_word(m.group()), text,
                      flags=re.IGNORECASE)  # fmt: skip
    return text


# A consultation stops linking the HCP to the patient once it is closed (or withdrawn, which
# clears the routing).
FINISHED_CONSULTATION = ("closed",)


def _patient_roles() -> frozenset[str]:
    from app.core.permissions import permissions_for
    from app.models.enums import Role

    return frozenset(r.value for r in Role if P.SELF_HEALTH_MANAGE in permissions_for(r))


# Roles whose accounts are patients' own (they hold their own health record), derived from the
# permission map rather than named.
PATIENT_ROLES = _patient_roles()


def is_patient_account(user) -> bool:
    """An account that belongs to a patient (its display name and email are patient PII)."""
    return getattr(user, "patient_id", None) is not None or (
        getattr(user, "role", None) in PATIENT_ROLES
    )


def mask_tree(value, fn):
    """Applies a text function to every string inside a JSON-like value (dicts, lists,
    tuples); other values are returned as they are."""
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, dict):
        return {k: mask_tree(v, fn) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_tree(v, fn) for v in value]
    return value


class PatientViewer:
    """What one reader may see of patients' PII during one request. Assignment sets are read
    once, lazily, so a list of a thousand patients costs three queries, not three thousand."""

    def __init__(self, db: Session, user: User | None):
        self.db = db
        self.user = user
        self._cm: set[str] | None = None
        self._hcp: set[str] | None = None

    def _cm_patients(self) -> set[str]:
        if self._cm is None:
            self._cm = set()
            if self.user is not None and can(self.user, P.PATIENT_READ_ASSIGNED):
                self._cm = set(
                    self.db.scalars(
                        select(CareManagerPatient.patient_id).where(
                            CareManagerPatient.care_manager_user_id == self.user.id
                        )
                    )
                )
        return self._cm

    def _hcp_patients(self) -> set[str]:
        if self._hcp is None:
            self._hcp = set()
            hcp_id = getattr(self.user, "hcp_id", None)
            if hcp_id and can(self.user, P.SELF_PATIENTS_READ):
                team = self.db.scalars(
                    select(PatientHcp.patient_id).where(PatientHcp.hcp_id == hcp_id)
                )
                routed = self.db.scalars(
                    select(CareRequest.patient_id).where(
                        CareRequest.assigned_hcp_id == hcp_id,
                        CareRequest.status.not_in(FINISHED_CONSULTATION),
                    )
                )
                self._hcp = set(team) | set(routed)
        return self._hcp

    def sees(self, patient_id: str | None) -> bool:
        """True when this reader may see the patient's PII unmasked."""
        if self.user is None or not patient_id:
            return False
        if getattr(self.user, "patient_id", None) == patient_id:
            return True
        return patient_id in self._cm_patients() or patient_id in self._hcp_patients()

    # --- values -------------------------------------------------------------------------------

    def name(self, p: Patient) -> str:
        full = f"{p.first_name} {p.last_name}".strip()
        return full if self.sees(p.patient_id) else mask_name(full)

    def first_name(self, p: Patient) -> str:
        return p.first_name if self.sees(p.patient_id) else mask_name(p.first_name)

    def name_of(self, patient_id: str | None, full: str | None) -> str | None:
        """A patient name already written out elsewhere (a user's display name, a target name)."""
        return full if self.sees(patient_id) else mask_name(full)

    def email(self, patient_id: str | None, email: str | None) -> str | None:
        return email if self.sees(patient_id) else mask_email(email)

    def birth_date(self, p: Patient):
        return p.birth_date if self.sees(p.patient_id) else None

    def zip(self, p: Patient) -> str | None:
        return p.zip if self.sees(p.patient_id) else None

    def text(self, p: Patient | None, text: str | None) -> str | None:
        """Free text about a patient: their own name masked unless this reader sees it."""
        if p is None or text is None or self.sees(p.patient_id):
            return text
        return mask_in_text(text, [p.first_name, p.last_name])

    def text_for(self, patient_id: str | None, text: str | None) -> str | None:
        if not patient_id or text is None or self.sees(patient_id):
            return text
        return self.text(self.db.get(Patient, patient_id), text)

    def scrub(self, p: Patient | None, value):
        """A whole response about one patient: their name masked in every string inside it
        unless this reader sees it. Pydantic models are rebuilt from the masked values."""
        if p is None or self.sees(p.patient_id):
            return value
        names = [p.first_name, p.last_name]
        if hasattr(value, "model_dump"):
            data = mask_tree(value.model_dump(), lambda s: mask_in_text(s, names))
            return type(value).model_validate(data)
        return mask_tree(value, lambda s: mask_in_text(s, names))

    def can_search_names(self) -> bool:
        """Name search runs only over patients this reader sees unmasked (see `name_filter`)."""
        return bool(self._cm_patients() or self._hcp_patients()
                    or getattr(self.user, "patient_id", None))  # fmt: skip

    def visible_ids(self) -> set[str]:
        own = getattr(self.user, "patient_id", None)
        return self._cm_patients() | self._hcp_patients() | ({own} if own else set())
