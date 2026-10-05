"""Re-runs the hard gates for one stored recommendation, against the database as it is now.

Used at approval and again at send: an MLR approval can lapse and a consent can be
withdrawn between the moment a recommendation was generated and the moment someone acts.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import clock
from app.core.engine_config import get_config
from app.features.engagement import EngagementState
from app.models import Consent, Content, Interaction, Nba, PatientTherapy
from app.models.enums import ReviewStatus, TargetType
from app.nba import gates


def current_failures(db: Session, nba: Nba, *, include_frequency: bool = True) -> list[str]:
    today = clock.get_today(db)
    content = db.get(Content, nba.content_id)
    consents = (
        db.scalars(select(Consent).where(Consent.patient_id == nba.target_id)).all()
        if nba.target_type == TargetType.PATIENT
        else []
    )
    state = EngagementState()
    for i in db.scalars(
        select(Interaction)
        .where(Interaction.target_type == nba.target_type, Interaction.target_id == nba.target_id)
        .order_by(Interaction.int_ts)
    ):
        state.touch(i.int_ts.date())
    failures = gates.evaluate(
        nba.target_type,
        content,
        nba.channel,
        today,
        list(consents),
        state,
        get_config(db, "frequency_caps"),
    )
    if not include_frequency:
        failures = [f for f in failures if f not in gates.FREQUENCY_CODES]
    therapy = db.get(PatientTherapy, nba.therapy_id) if nba.therapy_id else None
    if therapy is not None and therapy.review_status != ReviewStatus.CONFIRMED:
        failures.append(gates.THERAPY_INACTIVE)
    return failures
