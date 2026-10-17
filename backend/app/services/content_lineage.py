"""Content lineage context: the versions of one content item the reader may see, and how many
recommendations, options considered and deliveries reference it.

Every version passes the same `readable` check as the content screens; every reference count
is taken only over recommendations inside the reader's own recommendation scope
(`rbac.nba_filter`), so a role counts only what it may already see. Counts only: no target,
wording or reviewer. Read only.
"""

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.content import governance
from app.core import clock, rbac
from app.models import Interaction, Nba, NbaCandidate, User
from app.services import serializers as out
from app.services.content import readable, scoped_content
from app.services.schemas import Contract


class VersionRef(Contract):
    content_id: str
    version: int
    mlr_status: str
    effective_date: date | None
    expiry_date: date | None
    is_expired: bool
    usable: bool
    current: bool  # the version asked about


class ReferenceCount(Contract):
    kind: str  # recommendation | candidate | delivery
    status: str
    count: int


class ContentLineage(Contract):
    lineage_versions: list[VersionRef]
    hidden_versions: int  # versions in the lineage this reader may not see (count only)
    references: list[ReferenceCount]


def content_lineage(db: Session, user: User, content_id: str) -> ContentLineage:
    c = scoped_content(db, user, content_id)
    today = clock.get_today(db)

    versions = governance.versions(db, c.lineage_id) if c.lineage_id else [c]
    visible = [v for v in versions if readable(db, user, v, today)]
    refs: list[ReferenceCount] = []
    scoped = select(Nba.id).where(rbac.nba_filter(user))
    for status, n in db.execute(
        select(Nba.status, func.count())
        .where(Nba.content_id == c.content_id, rbac.nba_filter(user))
        .group_by(Nba.status)
        .order_by(Nba.status)
    ).all():
        refs.append(ReferenceCount(kind="recommendation", status=status, count=n))
    for eligible, n in db.execute(
        select(NbaCandidate.eligible, func.count())
        .where(NbaCandidate.content_id == c.content_id, NbaCandidate.nba_id.in_(scoped))
        .group_by(NbaCandidate.eligible)
        .order_by(NbaCandidate.eligible)
    ).all():
        refs.append(
            ReferenceCount(kind="candidate", status="eligible" if eligible else "gated", count=n)
        )
    for outcome, n in db.execute(
        select(Interaction.outcome, func.count())
        .where(Interaction.content_id == c.content_id, Interaction.nba_id.in_(scoped))
        .group_by(Interaction.outcome)
        .order_by(Interaction.outcome)
    ).all():
        refs.append(ReferenceCount(kind="delivery", status=outcome, count=n))
    return ContentLineage(
        lineage_versions=[
            VersionRef(
                **{
                    k: v
                    for k, v in out.content_out(x, today).items()
                    if k in VersionRef.model_fields
                },
                current=x.content_id == c.content_id,
            )
            for x in visible
        ],
        hidden_versions=len(versions) - len(visible),
        references=refs,
    )
