"""Sending an approved recommendation and capturing what came back.

Each channel sits behind an adapter with one method. The demo adapters only record the
hand-off; a production deployment replaces them with connectors to the client's CRM,
marketing-automation and telephony systems without touching anything else.
"""

from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.core import clock
from app.models import Interaction, MedicationFill, MessageDraft, Nba, PatientTherapy, User
from app.models.enums import Channel, NbaStatus, Outcome, TargetType
from app.models.tables import utcnow
from app.nba import gates
from app.nba.revalidate import current_failures

ENGAGED_OUTCOMES = (
    Outcome.OPENED, Outcome.CLICKED, Outcome.REPLIED, Outcome.COMPLETED, Outcome.FILLED,
)  # fmt: skip


class ChannelAdapter(Protocol):
    name: str

    def deliver(self, nba: Nba, draft: MessageDraft | None) -> str:
        """Hands the message to the channel. Returns a delivery reference."""


class SimulatedAdapter:
    """Stands in for an external system (email service, SMS gateway, dialler, CRM task)."""

    def __init__(self, channel: str) -> None:
        self.name = f"simulated_{channel}"

    def deliver(self, nba: Nba, draft: MessageDraft | None) -> str:
        return f"{self.name}:{nba.id}"


class PortalAdapter:
    """In-app delivery: the message appears in the recipient's portal inbox."""

    name = "portal_inbox"

    def deliver(self, nba: Nba, draft: MessageDraft | None) -> str:
        return f"{self.name}:{nba.id}"


ADAPTERS: dict[str, ChannelAdapter] = {
    Channel.PORTAL: PortalAdapter(),
    **{
        ch: SimulatedAdapter(ch)
        for ch in (Channel.EMAIL, Channel.SMS, Channel.PHONE, Channel.REP_VISIT)
    },
}


class SendBlocked(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def now_on(db: Session) -> datetime:
    """Now: today's date with the current time of day."""
    return datetime.combine(clock.get_today(db), utcnow().time().replace(microsecond=0))


def send(db: Session, nba: Nba, user: User) -> Interaction:
    """Delivers an approved recommendation. Gates are checked one last time first."""
    if nba.status != NbaStatus.APPROVED:
        raise ValueError(f"only approved recommendations can be sent (status: {nba.status})")

    failures = current_failures(db, nba)
    if failures:
        nba.status, nba.block_reason = NbaStatus.BLOCKED, gates.describe(failures)
        audit.record(
            db,
            "nba_blocked_at_send",
            "nba",
            nba.id,
            nba_id=nba.id,
            actor=user.username,
            actor_role=user.role,
            compliance_ok=gates.compliance_ok(failures),
            consent_ok=gates.consent_ok(failures),
            reason=nba.block_reason,
            detail={"gate_failures": failures},
        )
        raise SendBlocked(nba.block_reason)

    draft = db.scalar(
        select(MessageDraft).where(MessageDraft.nba_id == nba.id, MessageDraft.is_selected)
    )
    adapter = ADAPTERS[nba.channel]
    reference = adapter.deliver(nba, draft)
    interaction = Interaction(
        target_type=nba.target_type,
        target_id=nba.target_id,
        channel=nba.channel,
        int_ts=now_on(db),
        type=nba.action,
        outcome=Outcome.PENDING,
        content_id=nba.content_id,
        therapy_id=nba.therapy_id,
        nba_id=nba.id,
        source="nba",
        actor_user_id=user.id,
    )
    db.add(interaction)
    nba.status = NbaStatus.SENT
    audit.record(
        db,
        "nba_sent",
        "nba",
        nba.id,
        nba_id=nba.id,
        actor=user.username,
        actor_role=user.role,
        compliance_ok=True,
        consent_ok=True if nba.target_type == TargetType.PATIENT else None,
        reason="All gates passed at send",
        detail={
            "adapter": adapter.name,
            "reference": reference,
            "draft_id": draft.id if draft else None,
        },
    )
    db.flush()
    return interaction


def record_fill(
    db: Session, therapy: PatientTherapy, when: datetime, source: str = "claims"
) -> MedicationFill:
    fill = MedicationFill(
        patient_id=therapy.patient_id,
        therapy_id=therapy.id,
        drug_name=therapy.drug_name,
        fill_date=when.date(),
        days_supply=therapy.days_supply,
        quantity=therapy.days_supply,
        copay=therapy.copay,
        source=source,
    )
    db.add(fill)
    return fill


def capture_response(
    db: Session,
    interaction: Interaction,
    outcome: str,
    when: datetime,
    *,
    actor: str,
    actor_role: str,
    note: str | None = None,
) -> Interaction:
    """Records how a sent recommendation landed. The signal the next cycle learns from."""
    interaction.outcome, interaction.outcome_ts = outcome, when
    if interaction.nba_id:
        nba = db.get(Nba, interaction.nba_id)
        if outcome in ENGAGED_OUTCOMES:
            nba.status = NbaStatus.RESPONDED
        audit.record(
            db,
            "response_captured",
            "nba",
            nba.id,
            nba_id=nba.id,
            actor=actor,
            actor_role=actor_role,
            reason=note,
            detail={"outcome": str(outcome), "channel": interaction.channel},
        )
    db.flush()
    return interaction
