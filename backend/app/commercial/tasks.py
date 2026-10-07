"""Commercial work a representative owes an HCP: the HCP's own request, a follow-up, a meeting.

Visibility follows the assignment (`rep_hcp`): every representative currently assigned to the
HCP sees its open work, and the owner is re-resolved whenever the assignment changes, so nothing
stays with a representative who was moved, disabled or deleted. Completing a meeting or a call
records an interaction, which then counts for the contact limits and for learning like any other.
"""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app import audit
from app.core import clock, rbac
from app.core.permissions import ROLE_PERMISSIONS, Permission, can
from app.models import Content, Hcp, HcpTask, Interaction, RepHcp, User
from app.models.enums import AccountStatus, Channel, Outcome, TargetType
from app.models.tables import utcnow

KINDS = ("hcp_request", "follow_up", "meeting")
# Roles that work an assigned HCP panel (from the permission map, never a role name).
REP_ROLES = [r for r, perms in ROLE_PERMISSIONS.items() if Permission.HCP_READ_ASSIGNED in perms]
OPEN = ("open", "scheduled")
INTENTS = {
    "interested": "Interested",
    "request_meeting": "Requests a meeting",
    "need_info": "Needs more information",
    "need_evidence": "Needs supporting evidence",
    "not_now": "Not now",
    "decline": "Not interested",
}
OUTCOME_REASONS = {
    "interested": "Interested",
    "need_info": "Needs more information",
    "another_meeting": "Wants another meeting",
    "follow_up": "Follow-up required",
    "not_now": "Not now",
}
# Reasons that leave work to do.
FOLLOW_UP_REASONS = ("interested", "need_info", "another_meeting", "follow_up", "not_now")
MODES = {"in_person": Channel.REP_VISIT, "phone": "phone", "video": Channel.REP_VISIT}
NOT_NOW_DAYS = 30


def error(code: str, message: str, http: int = status.HTTP_409_CONFLICT, **extra):
    return HTTPException(http, {"code": code, "message": message, **extra})


# --- Ownership -------------------------------------------------------------------------


def assigned_reps(db: Session, hcp_id: str) -> list[int]:
    """Active representatives currently assigned to the HCP, in a stable order."""
    return list(
        db.scalars(
            select(RepHcp.rep_user_id)
            .join(User, User.id == RepHcp.rep_user_id)
            .where(
                RepHcp.hcp_id == hcp_id,
                User.status == AccountStatus.ACTIVE,
                User.role.in_(REP_ROLES),
            )
            .order_by(RepHcp.rep_user_id)
        )
    )


def owner_for(db: Session, hcp_id: str, preferred: int | None) -> int | None:
    reps = assigned_reps(db, hcp_id)
    if preferred in reps:
        return preferred
    return reps[0] if reps else None


def reassign_open_work(db: Session, hcp_ids: list[str] | None = None) -> int:
    """Re-resolve owners of open work, e.g. after an assignment or account change."""
    query = select(HcpTask).where(HcpTask.status.in_(OPEN))
    if hcp_ids is not None:
        query = query.where(HcpTask.hcp_id.in_(hcp_ids))
    moved = 0
    for task in db.scalars(query):
        owner = owner_for(db, task.hcp_id, task.owner_user_id)
        if owner != task.owner_user_id:
            previous, task.owner_user_id = task.owner_user_id, owner
            moved += 1
            audit.record(
                db,
                "hcp_task_reassigned",
                "hcp_task",
                task.id,
                detail={"hcp_id": task.hcp_id, "from_user": previous, "to_user": owner},
            )
    return moved


def visible(user: User) -> Select:
    """Tasks a representative may see: those for HCPs currently assigned to them."""
    query = select(HcpTask)
    if can(user, Permission.HCP_READ_ALL):
        return query
    return query.where(HcpTask.hcp_id.in_(rbac.assigned_hcp_ids(user)))


def assigned_hcp(db: Session, user: User, hcp_id: str) -> Hcp:
    hcp = db.scalar(select(Hcp).where(Hcp.hcp_id == hcp_id, rbac.hcp_filter(user, Hcp.hcp_id)))
    if hcp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "HCP not found")
    return hcp


def _audit(db: Session, user: User | None, action: str, task: HcpTask, **detail):
    audit.record(
        db,
        action,
        "hcp_task",
        task.id,
        actor=user.username if user else "system",
        actor_role=user.role if user else "system",
        reason=detail.pop("reason", None),
        detail={"hcp_id": task.hcp_id, "kind": task.kind, "status": task.status, **detail},
    )


def _content(db: Session, content_id: str | None) -> Content | None:
    if not content_id:
        return None
    c = db.get(Content, content_id)
    if c is None or c.audience != TargetType.HCP:
        raise error("invalid_field", "Unknown HCP content.", 422)
    return c


# --- HCP intent ------------------------------------------------------------------------


def from_intent(
    db: Session, hcp_user: User, interaction: Interaction, intent: str, note: str | None
):
    """The HCP's stated intent on a delivered item: recorded on the delivery and turned into
    work for the assigned representative."""
    if intent not in INTENTS:
        raise error("invalid_field", "Choose one of the offered answers.", 422)
    interaction.intent = intent
    interaction.note = (note or "").strip()[:500] or None
    owner = owner_for(db, interaction.target_id, interaction.actor_user_id)
    today = clock.get_today(db)
    if intent == "decline":
        task = None
    else:
        task = HcpTask(
            hcp_id=interaction.target_id,
            kind="follow_up" if intent == "not_now" else "hcp_request",
            status="open",
            intent=intent,
            reason=INTENTS[intent],
            note=interaction.note,
            content_id=interaction.content_id,
            interaction_id=interaction.id,
            nba_id=interaction.nba_id,
            owner_user_id=owner,
            created_by_user_id=hcp_user.id,
            created_by_role=hcp_user.role,
            due_date=today + timedelta(days=NOT_NOW_DAYS) if intent == "not_now" else today,
            requested_by_hcp=intent == "request_meeting",
        )
        db.add(task)
        db.flush()
        _audit(db, hcp_user, "hcp_intent_received", task, intent=intent)
    return task


# --- Representative actions --------------------------------------------------------------


def _parse_when(when: str, tz: str) -> tuple[datetime, str]:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise error("invalid_field", "Unknown time zone.", 422) from exc
    try:
        local = datetime.fromisoformat(when)
    except ValueError as exc:
        raise error("invalid_field", "Give the date and time of the meeting.", 422) from exc
    if local.tzinfo is None:
        local = local.replace(tzinfo=zone)
    return local.astimezone(UTC).replace(tzinfo=None), tz


def create(db: Session, user: User, body: dict) -> HcpTask:
    """A follow-up, a meeting or a call the representative records."""
    from app.commercial.contact import contact_window

    hcp = assigned_hcp(db, user, body.get("hcp_id") or "")
    kind = body.get("kind")
    if kind not in ("follow_up", "meeting"):
        raise error("invalid_field", "Choose a follow-up or a meeting.", 422)
    content = _content(db, body.get("content_id"))
    reason = (body.get("reason") or "").strip()[:500]
    if not reason:
        raise error("invalid_field", "Say what the follow-up or meeting is for.", 422)
    task = HcpTask(
        hcp_id=hcp.hcp_id,
        kind=kind,
        reason=reason,
        note=(body.get("note") or "").strip()[:500] or None,
        content_id=content.content_id if content else None,
        owner_user_id=owner_for(db, hcp.hcp_id, user.id),
        created_by_user_id=user.id,
        created_by_role=user.role,
    )
    source = db.get(HcpTask, body["source_task_id"]) if body.get("source_task_id") else None
    if source is not None and source.hcp_id != hcp.hcp_id:
        source = None
    today = clock.get_today(db)
    if kind == "follow_up":
        try:
            due = date.fromisoformat(body.get("due_date") or "")
        except ValueError as exc:
            raise error("invalid_field", "Give the date the follow-up is due.", 422) from exc
        if due < today:
            raise error("invalid_field", "A follow-up cannot be due in the past.", 422)
        task.status, task.due_date = "open", due
    else:
        at, tz = _parse_when(body.get("scheduled_at") or "", body.get("timezone") or "UTC")
        if at <= utcnow():
            raise error("invalid_field", "A meeting must be in the future.", 422)
        mode = body.get("mode") or "in_person"
        if mode not in MODES:
            raise error("invalid_field", "Choose in person, phone or video.", 422)
        requested = bool(source and source.requested_by_hcp) or bool(
            source and source.intent == "request_meeting"
        )
        if not requested:
            window = contact_window(db, hcp)
            if at.date() < window["next_allowed"]:
                raise error(
                    "contact_gap",
                    f"Too soon after the last contact: meetings you propose can be from "
                    f"{window['next_allowed']:%d %b %Y}.",
                    next_allowed=window["next_allowed"].isoformat(),
                )
        task.status, task.scheduled_at, task.timezone, task.mode = "scheduled", at, tz, mode
        task.requested_by_hcp = requested
        task.due_date = at.date()
    if source is not None:
        task.interaction_id, task.nba_id = source.interaction_id, source.nba_id
        task.intent = source.intent
        task.content_id = task.content_id or source.content_id
    db.add(task)
    db.flush()
    _audit(db, user, f"hcp_{kind}_created", task, source_task_id=source.id if source else None)
    if source is not None and source.status in OPEN:
        # Answering an HCP's request with a meeting or a follow-up settles the request.
        source.status, source.completed_at = "done", utcnow()
        source.outcome = "answered"
        _audit(db, user, "hcp_task_completed", source, answered_by=task.id)
    return task


def update(db: Session, user: User, task: HcpTask, body: dict) -> HcpTask:
    """Complete, reschedule or cancel."""
    action = body.get("action")
    if task.status not in OPEN:
        raise error("not_open", "This item is already closed.")
    if action == "cancel":
        reason = (body.get("note") or "").strip()
        if not reason:
            raise error("invalid_field", "Say why it is cancelled.", 422)
        task.status, task.note, task.completed_at = "cancelled", reason[:500], utcnow()
        _audit(db, user, "hcp_task_cancelled", task, reason=reason)
        return task
    if action == "reschedule":
        if task.kind == "meeting":
            at, tz = _parse_when(
                body.get("scheduled_at") or "", body.get("timezone") or task.timezone or "UTC"
            )
            if at <= utcnow():
                raise error("invalid_field", "A meeting must be in the future.", 422)
            if not task.requested_by_hcp:
                from app.commercial.contact import contact_window

                window = contact_window(db, db.get(Hcp, task.hcp_id))
                if at.date() < window["next_allowed"]:
                    raise error(
                        "contact_gap",
                        f"Meetings you propose can be from {window['next_allowed']:%d %b %Y}.",
                        next_allowed=window["next_allowed"].isoformat(),
                    )
            previous = task.scheduled_at
            task.scheduled_at, task.timezone, task.due_date = at, tz, at.date()
            _audit(db, user, "hcp_task_rescheduled", task, previous=str(previous), new=str(at))
        else:
            try:
                due = date.fromisoformat(body.get("due_date") or "")
            except ValueError as exc:
                raise error("invalid_field", "Give the new due date.", 422) from exc
            if due < clock.get_today(db):
                raise error("invalid_field", "A follow-up cannot be due in the past.", 422)
            previous, task.due_date = task.due_date, due
            _audit(db, user, "hcp_task_rescheduled", task, previous=str(previous), new=str(due))
        return task
    if action != "complete":
        raise error("invalid_field", "Unknown action.", 422)
    outcome = body.get("outcome") or "completed"
    if outcome not in (Outcome.COMPLETED, Outcome.NO_RESPONSE, Outcome.DECLINED, "answered"):
        raise error("invalid_field", "Unknown outcome.", 422)
    reason = body.get("outcome_reason") or None
    if reason is not None and reason not in OUTCOME_REASONS:
        raise error("invalid_field", "Unknown outcome reason.", 422)
    note = (body.get("note") or "").strip()[:500] or None
    task.status, task.completed_at = "done", utcnow()
    task.outcome, task.outcome_reason = outcome, reason
    if note:
        task.note = note
    if task.kind == "meeting":
        from app.engagement.delivery import now_on

        # A meeting or call held is a contact: it counts for the limits and for learning.
        interaction = Interaction(
            target_type=TargetType.HCP,
            target_id=task.hcp_id,
            channel=MODES.get(task.mode or "in_person", Channel.REP_VISIT),
            int_ts=now_on(db),
            type="call" if task.mode == "phone" else "rep_visit",
            outcome=outcome if outcome != "answered" else Outcome.COMPLETED,
            outcome_ts=now_on(db),
            content_id=task.content_id,
            source="rep",
            actor_user_id=user.id,
            outcome_reason=reason,
            note=note,
        )
        db.add(interaction)
        db.flush()
        task.interaction_id = interaction.id
    _audit(db, user, "hcp_task_completed", task, outcome=outcome, outcome_reason=reason)
    return task


def task_out(db: Session, user: User, t: HcpTask, names: dict[int, str] | None = None) -> dict:
    hcp = db.get(Hcp, t.hcp_id)
    content = db.get(Content, t.content_id) if t.content_id else None
    owner = db.get(User, t.owner_user_id) if t.owner_user_id else None
    today = clock.get_today(db)
    overdue = t.status in OPEN and t.due_date is not None and t.due_date < today
    return {
        "id": t.id,
        "hcp_id": t.hcp_id,
        "hcp_name": _hcp_name(hcp),
        "kind": t.kind,
        "status": t.status,
        "intent": t.intent,
        "intent_label": INTENTS.get(t.intent) if t.intent else None,
        "reason": t.reason,
        "note": t.note,
        "content": None
        if content is None
        else {
            "content_id": content.content_id,
            "version": content.version,
            "title": content.title,
            "product": content.product,
            "status": content.mlr_status,
        },
        "owner": "You" if t.owner_user_id == user.id else (owner.display_name if owner else None),
        "yours": t.owner_user_id == user.id,
        "from_hcp": t.created_by_role == "hcp",
        "due_date": t.due_date,
        "scheduled_at": t.scheduled_at,
        "timezone": t.timezone,
        "mode": t.mode,
        "requested_by_hcp": t.requested_by_hcp,
        "overdue": overdue,
        "due_today": t.status in OPEN and t.due_date == today,
        "outcome": t.outcome,
        "outcome_reason": t.outcome_reason,
        "completed_at": t.completed_at,
        "created_at": t.created_at,
        "nba_id": t.nba_id,
        "interaction_id": t.interaction_id,
    }


def _hcp_name(hcp: Hcp | None) -> str | None:
    if hcp is None:
        return None
    from app.api.serializers import hcp_name

    return hcp_name(hcp)
