"""The hand-off to a CRM or marketing-automation system: a governed, read-only feed.

The engine sits on top of a client's CRM (field guide §08, §09): it decides and explains, a
person approves, and the downstream system executes and records. This module is that boundary
in its first form, with no connection to a real CRM: one record per recommendation that a
person approved (and, once delivered, its delivery and outcome), exported as JSON or CSV.

What crosses the boundary is fixed by `FIELDS` (the documented field map) and nothing else:

- Stable identifiers (`record_id` = "NBA-<id>", content id and version, cycle id), status,
  scheduled, approval, delivery and outcome times (UTC, ISO 8601), action, channel, origin.
- HCP records add the HCP id and NPI (a public registry number) and the content title.
- Patient records carry only the internal pseudonymous patient id. Never a name, date of
  birth, contact detail, condition, medicine name, adherence figure, delivered wording or
  rationale text: those stay inside the engine.
- `synthetic` says whether the record belongs to the synthetic demo population.

Scope is the reader's own recommendation scope (`core/rbac.nba_filter`); every export is
audited with its parameters and row count. Recommendations nobody approved (generated, ready,
blocked, rejected, expired) are never handed off.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import rbac
from app.models import Content, Hcp, Interaction, Nba, Patient, User
from app.models.enums import NbaStatus, PatientOrigin, TargetType
from app.nba.rationale import ACTION_LABEL

HANDED_OFF = (NbaStatus.APPROVED, NbaStatus.SENT, NbaStatus.RESPONDED)
MAX_ROWS = 5000
MAX_RANGE_DAYS = 366
DEFAULT_RANGE_DAYS = 30


@dataclass(frozen=True)
class Field:
    name: str
    type: str
    description: str
    crm_use: str
    hcp_only: bool = False


FIELDS: tuple[Field, ...] = (
    Field(
        "record_id",
        "string",
        "Stable id of this hand-off record (NBA-<id>)",
        "External id / upsert key",
    ),
    Field(
        "recommendation_id",
        "integer",
        "Recommendation id in the engine",
        "Reference back to the engine",
    ),
    Field("cycle_id", "integer", "Engine cycle that produced it", "Batch reference"),
    Field(
        "origin",
        "string",
        "engine, or rep (proposed by a representative)",
        "Source of the suggestion",
    ),
    Field("target_type", "string", "HCP or PATIENT", "Which object the record attaches to"),
    Field(
        "target_id",
        "string",
        "Internal id (HCP_..., or a pseudonymous PAT_... id)",
        "Account / contact match key",
    ),
    Field(
        "hcp_npi",
        "string",
        "NPI of the HCP (public registry number)",
        "Account match key",
        hcp_only=True,
    ),
    Field(
        "action",
        "string",
        "Action code (refill_nudge, share_study, ...)",
        "Suggestion or task type",
    ),
    Field("action_label", "string", "Action in words", "Suggestion title"),
    Field("channel", "string", "email, sms, portal, phone or rep_visit", "Channel / activity type"),
    Field("content_id", "string", "MLR-approved content id", "Approved document reference"),
    Field("content_version", "integer", "Version of that content", "Approved document version"),
    Field(
        "content_title", "string", "Title of the approved content", "Document name", hcp_only=True
    ),
    Field("status", "string", "approved, sent or responded", "Suggestion / task status"),
    Field("scheduled_for", "date", "Day the action is due (never before it)", "Due date"),
    Field("approved_at", "datetime", "When a person approved it (UTC)", "Accepted date"),
    Field("approved_by_role", "string", "Role of the approver, never their identity", "Owner role"),
    Field("sent_at", "datetime", "When it was delivered (UTC), if delivered", "Activity date"),
    Field(
        "outcome",
        "string",
        "Captured response, if any. HCP: as captured (opened, clicked, replied, ...). "
        "Patient: engaged, no_response or declined only",
        "Activity outcome",
    ),
    Field("outcome_at", "datetime", "When the response was captured (UTC)", "Outcome date"),
    Field("synthetic", "boolean", "True for the synthetic demo population", "Test-data flag"),
)
FIELD_NAMES = [f.name for f in FIELDS]


class ExportError(ValueError):
    pass


@dataclass(frozen=True)
class ExportParams:
    target_type: str | None
    status: str | None
    since: date
    until: date
    limit: int


def validate(
    target_type: str | None,
    status: str | None,
    since: date | None,
    until: date | None,
    limit: int,
    today: date,
) -> ExportParams:
    if target_type not in (None, TargetType.HCP, TargetType.PATIENT):
        raise ExportError("target_type must be HCP or PATIENT.")
    if status not in (None, *HANDED_OFF):
        raise ExportError("status must be approved, sent or responded.")
    until = until or today
    since = since or until - timedelta(days=DEFAULT_RANGE_DAYS)
    if since > until:
        raise ExportError("since must not be after until.")
    if (until - since).days > MAX_RANGE_DAYS:
        raise ExportError(f"The date range may cover at most {MAX_RANGE_DAYS} days.")
    if not 1 <= limit <= MAX_ROWS:
        raise ExportError(f"limit must be between 1 and {MAX_ROWS}.")
    return ExportParams(target_type, status, since, until, limit)


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def records(db: Session, user: User, params: ExportParams) -> list[dict]:
    """Approved recommendations in the reader's scope, approved within the date range."""
    approved_at = func.coalesce(Nba.reviewed_ts, Nba.created_ts)
    query = (
        select(Nba)
        .where(
            Nba.status.in_([params.status] if params.status else HANDED_OFF),
            rbac.nba_filter(user),
            approved_at >= datetime.combine(params.since, time.min),
            approved_at < datetime.combine(params.until + timedelta(days=1), time.min),
        )
        .order_by(Nba.id)
        .limit(params.limit)
    )
    if params.target_type:
        query = query.where(Nba.target_type == params.target_type)
    rows = db.scalars(query).all()
    if not rows:
        return []
    ids = [n.id for n in rows]
    deliveries = {
        i.nba_id: i
        for i in db.scalars(
            select(Interaction).where(Interaction.nba_id.in_(ids)).order_by(Interaction.id)
        )
    }
    contents = {
        c.content_id: c
        for c in db.scalars(
            select(Content).where(Content.content_id.in_({n.content_id for n in rows}))
        )
    }
    hcps = {
        h.hcp_id: h
        for h in db.scalars(
            select(Hcp).where(
                Hcp.hcp_id.in_({n.target_id for n in rows if n.target_type == TargetType.HCP})
            )
        )
    }
    patient_origin = dict(
        db.execute(
            select(Patient.patient_id, Patient.origin).where(
                Patient.patient_id.in_(
                    {n.target_id for n in rows if n.target_type == TargetType.PATIENT}
                )
            )
        ).all()
    )
    roles = dict(
        db.execute(
            select(User.id, User.role).where(
                User.id.in_({n.reviewed_by_user_id for n in rows if n.reviewed_by_user_id})
            )
        ).all()
    )
    out = []
    for n in rows:
        hcp_side = n.target_type == TargetType.HCP
        content = contents.get(n.content_id)
        delivery = deliveries.get(n.id)
        hcp = hcps.get(n.target_id) if hcp_side else None
        synthetic = (
            (hcp is not None and hcp.origin == "synthetic")
            if hcp_side
            else patient_origin.get(n.target_id) == PatientOrigin.SYNTHETIC
        )
        delivered = delivery is not None
        out.append(
            {
                "record_id": f"NBA-{n.id}",
                "recommendation_id": n.id,
                "cycle_id": n.cycle_id,
                "origin": n.origin,
                "target_type": n.target_type,
                "target_id": n.target_id,
                "hcp_npi": hcp.npi if hcp else None,
                "action": n.action,
                "action_label": ACTION_LABEL.get(n.action, n.action),
                "channel": n.channel,
                "content_id": n.content_id,
                "content_version": content.version if content else None,
                "content_title": content.title if (content and hcp_side) else None,
                "status": n.status,
                "scheduled_for": _iso(n.scheduled_for),
                "approved_at": _iso(n.reviewed_ts or n.created_ts),
                "approved_by_role": roles.get(n.reviewed_by_user_id),
                "sent_at": _iso(delivery.int_ts) if delivered else None,
                "outcome": _outcome(delivery, hcp_side) if delivered else None,
                "outcome_at": _iso(delivery.outcome_ts) if delivered else None,
                "synthetic": synthetic,
            }
        )
    return out


def _outcome(delivery: Interaction, hcp_side: bool) -> str | None:
    """HCP responses as captured. A patient's response is generalised to engaged / no
    response / declined: whether they refilled a medicine is health information and stays in
    the engine."""
    if delivery.outcome == "pending":
        return None
    if hcp_side or delivery.outcome in ("no_response", "declined"):
        return delivery.outcome
    return "engaged"


# A cell starting with one of these could run as a formula when the file is opened in a
# spreadsheet: it is prefixed with an apostrophe.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(FIELD_NAMES)
    for row in rows:
        writer.writerow([_cell(row[name]) for name in FIELD_NAMES])
    return buffer.getvalue()


def field_map() -> list[dict]:
    return [
        {
            "name": f.name,
            "type": f.type,
            "description": f.description,
            "crm_use": f.crm_use,
            "hcp_only": f.hcp_only,
        }
        for f in FIELDS
    ]
