"""HCP context: the scoped HCP record and HCP 360."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import panel_adherence, pipeline, unify
from app.clinical import hcps as hcp_records
from app.commercial import contact, tasks
from app.core import clock, rbac
from app.core.permissions import Permission, can_any
from app.models import Hcp, HcpTask, User
from app.models.enums import TargetType
from app.services import common
from app.services import serializers as out
from app.services.errors import Forbidden, NotFound
from app.services.schemas import (
    ContactWindow,
    HcpProfile,
    HcpSources,
    PanelAdherence,
    SpecialtyOut,
)

READ = (Permission.HCP_READ_ALL, Permission.HCP_READ_ASSIGNED)


def scoped_hcp(db: Session, user: User, hcp_id: str) -> Hcp:
    """The HCP record, if this staff reader may see it in full."""
    if not can_any(user, *READ):
        raise Forbidden("Your account cannot read HCP records.")
    h = db.scalar(select(Hcp).where(Hcp.hcp_id == hcp_id, rbac.hcp_filter(user, Hcp.hcp_id)))
    if h is None:
        raise NotFound("HCP not found")
    return h


def panel(db: Session, h: Hcp) -> PanelAdherence:
    """How this HCP's consenting patients are getting on, as de-identified groups (see
    panel_adherence.py): from the latest cycle's snapshot when one is valid, otherwise
    computed now."""
    from app.analytics import snapshots

    stored = snapshots.panel_for(db, h.hcp_id)
    if stored is not None:
        return PanelAdherence(**stored)
    settings = pipeline.engine_settings(db)
    pts = panel_adherence.points_from_db(db, clock.get_today(db), settings)
    computed = panel_adherence.panel(h, panel_adherence.attributed_ids(db, h.hcp_id), pts, settings)
    return PanelAdherence(**computed)


def hcp_profile(db: Session, user: User, hcp_id: str) -> HcpProfile:
    h = scoped_hcp(db, user, hcp_id)
    today = clock.get_today(db)
    return HcpProfile(
        hcp_id=h.hcp_id,
        npi=h.npi,
        name=out.hcp_name(h),
        specialties=[SpecialtyOut(**s) for s in hcp_records.specialty_out(db, h.hcp_id)],
        origin=h.origin,
        taxonomy_code=h.taxonomy_code,
        organization=h.organization,
        city=h.city,
        state=h.state,
        country=h.country,
        # City, state and country as known (an invited HCP: the country they gave).
        location=out.hcp_location_label(h),
        rx_volume_annual=h.rx_volume_annual,
        segment=h.segment,
        value_score=h.value_score,
        channel_affinity=h.channel_affinity,
        as_of_date=today,
        features=out.features_out(db, TargetType.HCP, hcp_id, today),
        interactions=common.history(db, TargetType.HCP, hcp_id, reader=user),
        open_nba=common.open_recommendation(db, TargetType.HCP, hcp_id),
        # De-identified group adherence of consenting patients (suppressed below the minimum).
        panel_adherence=panel(db, h),
        # Upstream records resolved to this profile (synthetic identity-resolution feeds).
        sources=HcpSources(**unify.for_hcp(db, h)),
        # When the contact limits allow the next touch, and the open commercial work.
        contact_window=ContactWindow(**contact.contact_window(db, h)),
        open_work=[
            tasks.task_out(db, user, t)
            for t in db.scalars(
                select(HcpTask)
                .where(HcpTask.hcp_id == hcp_id, HcpTask.status.in_(tasks.OPEN))
                .order_by(HcpTask.due_date, HcpTask.id)
            )
        ],
    )
