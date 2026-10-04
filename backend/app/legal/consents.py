"""Who accepted or agreed to what, which version, when, and what is still outstanding.

Every change is a new `consent_record` row; nothing is updated or deleted. An account is
"pending" while any consent it requires is missing, withdrawn, or for a version older than
the minimum (a new document version that required re-acceptance). While pending, the API
refuses everything except reading the documents, accepting, privacy requests, export and
sign-out (`api/deps.get_current_user`).

Patient outreach consent per channel and provider sharing are a different thing and keep
their own model (the `consent` table, checked by the engine at every step).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth.errors import AuthError
from app.core import jurisdiction
from app.core.permissions import Permission, can
from app.legal import registry
from app.legal.requests import create_request
from app.models import ConsentRecord, User
from app.models.tables import utcnow

ACCEPTED, WITHDRAWN = "accepted", "withdrawn"
SOURCES = {"signup", "invitation", "settings", "reacceptance"}


def required_kinds(user: User) -> list[str]:
    kinds = ["terms", "privacy_ack"]
    # Accounts linked to their own patient record: their health information is processed.
    if can(user, Permission.SELF_CONSENT_MANAGE):
        kinds.append("health_data")
    return kinds


def latest(db: Session, user: User) -> dict[str, ConsentRecord]:
    out: dict[str, ConsentRecord] = {}
    for row in db.scalars(
        select(ConsentRecord).where(ConsentRecord.user_id == user.id).order_by(ConsentRecord.id)
    ):
        out[row.kind] = row
    return out


def _satisfied(kind: str, row: ConsentRecord | None) -> bool:
    if row is None or row.action != ACCEPTED:
        return False
    spec = registry.CONSENT_KINDS[kind]
    if "document" not in spec:
        return True
    document = spec["document"]
    if row.version not in registry.VERSIONS[document]:
        return False
    return registry.version_index(document, row.version) >= registry.version_index(
        document, registry.minimum_accepted_version(document)
    )


def pending(db: Session, user: User) -> list[str]:
    current = latest(db, user)
    return [kind for kind in required_kinds(user) if not _satisfied(kind, current.get(kind))]


def _record(db: Session, user: User, kind: str, action: str, source: str) -> ConsentRecord:
    row = ConsentRecord(
        user_id=user.id,
        kind=kind,
        version=registry.consent_version(kind),
        action=action,
        jurisdiction=jurisdiction.code(user.country, user.region),
        source=source,
        created_at=utcnow(),
    )
    db.add(row)
    audit.record(
        db,
        "consent_accepted" if action == ACCEPTED else "consent_withdrawn",
        "user",
        user.id,
        actor=user.username,
        actor_role=user.role,
        detail={"kind": kind, "version": row.version, "source": source},
    )
    return row


def accept(db: Session, user: User, kinds: list[str], source: str) -> list[str]:
    """Records acceptance of the current version of each kind. Only kinds this account is
    asked for can be accepted."""
    allowed = set(required_kinds(user))
    unknown = [k for k in kinds if k not in allowed]
    if unknown or not kinds:
        raise AuthError(422, "invalid_consent", "That consent does not apply to this account.")
    if source not in SOURCES:
        raise AuthError(422, "invalid_consent", "Unknown consent source.")
    for kind in dict.fromkeys(kinds):
        _record(db, user, kind, ACCEPTED, source)
    db.flush()
    return pending(db, user)


def withdraw(db: Session, user: User, kind: str) -> None:
    """Withdrawing is as easy as giving. Withdrawing consent to process health information
    restricts the account until it is given again, and opens a restriction request so a
    person reviews what happens to the existing data; nothing is deleted automatically."""
    spec = registry.CONSENT_KINDS.get(kind)
    if not spec or not spec.get("withdrawable") or kind not in required_kinds(user):
        raise AuthError(
            422,
            "not_withdrawable",
            "This cannot be withdrawn here. To stop using the service, request account "
            "deletion instead.",
        )
    current = latest(db, user).get(kind)
    if current is None or current.action != ACCEPTED:
        raise AuthError(409, "already_withdrawn", "This consent is not currently given.")
    _record(db, user, kind, WITHDRAWN, "settings")
    create_request(
        db,
        user,
        "restriction",
        "Automatic: consent to process health information was withdrawn. Review whether "
        "existing data must be restricted or erased.",
    )
    db.flush()


def history(db: Session, user: User) -> list[dict]:
    return [
        {
            "kind": r.kind,
            "label": registry.CONSENT_KINDS.get(r.kind, {}).get("label", r.kind),
            "version": r.version,
            "action": r.action,
            "source": r.source,
            "jurisdiction": r.jurisdiction,
            "at": r.created_at,
        }
        for r in db.scalars(
            select(ConsentRecord)
            .where(ConsentRecord.user_id == user.id)
            .order_by(ConsentRecord.id.desc())
        )
    ]


def status(db: Session, user: User) -> dict:
    current = latest(db, user)
    items = []
    for kind in required_kinds(user):
        spec = registry.CONSENT_KINDS[kind]
        row = current.get(kind)
        items.append(
            {
                "kind": kind,
                "label": spec["label"],
                "statement": spec.get("statement"),
                "document": spec.get("document"),
                "current_version": registry.consent_version(kind),
                "state": row.action if row else None,
                "version": row.version if row else None,
                "at": row.created_at if row else None,
                "satisfied": _satisfied(kind, row),
                "withdrawable": bool(spec.get("withdrawable")),
            }
        )
    return {"consents": items, "pending": pending(db, user)}
