"""Summaries computed once after a successful cycle and read many times.

What is published (all aggregates; no patient appears in any of them):

  panel_adherence  per HCP in the engine population   (HCP 360)
  learning         per audience x segment x source    (Outcomes -> What worked, default range)
  like_for_like    one                                (Dashboard)
  measures         all patients                       (Outcomes -> Measures, all-patient scope)

A snapshot is used only while all of these hold, otherwise the reader computes live:

- it belongs to the latest *finished* engine cycle (a failed or unfinished cycle publishes
  nothing, and an older cycle's snapshot is never shown as current);
- its calculation version is the current one (`CALC_VERSION`);
- it was computed for today (figures are "as of today" everywhere else too);
- for panel figures, no aggregate-statistics consent has changed since it was computed, so a
  patient who opts out leaves the figures at once, not at the next cycle.

Publishing happens in its own transaction after the cycle committed; if it fails, the
previous snapshots stay in the table but no longer qualify, and readers compute live.
Safety-critical checks (consent at send, MLR, contact limits, authorization) never read
snapshots: they are evaluated live where they act.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import clock
from app.models import Consent, EngineCycle, SummarySnapshot
from app.models.enums import ConsentPurpose

log = logging.getLogger("nba.snapshots")

CALC_VERSION = 1
LEARNING_DIMENSIONS = {
    "PATIENT": ("situation", "measure", "plan_type"),
    "HCP": ("segment", "specialty"),
}
LEARNING_SOURCES = ("all", "engine", "baseline")


def latest_cycle_id(db: Session) -> int | None:
    return db.scalar(select(func.max(EngineCycle.id)).where(EngineCycle.finished_ts.is_not(None)))


def consent_mark(db: Session) -> str:
    """Changes whenever an aggregate-statistics consent is recorded (rows are append-only;
    an erasure removes rows, which changes the count)."""
    last, count = db.execute(
        select(func.max(Consent.id), func.count()).where(
            Consent.purpose == ConsentPurpose.AGGREGATE_INSIGHTS
        )
    ).one()
    return f"{last or 0}:{count}"


def get(db: Session, kind: str, scope_key: str, *, mark: str | None = None):
    cycle_id = latest_cycle_id(db)
    if cycle_id is None:
        return None
    row = db.scalar(
        select(SummarySnapshot).where(
            SummarySnapshot.kind == kind,
            SummarySnapshot.scope_key == scope_key,
            SummarySnapshot.cycle_id == cycle_id,
            SummarySnapshot.calc_version == CALC_VERSION,
            SummarySnapshot.as_of == clock.get_today(db),
        )
    )
    if row is None or (mark is not None and row.source_mark != mark):
        return None
    return row.payload


def _json(value):
    """Dates and other values as the API would send them (the payload is JSON)."""
    from fastapi.encoders import jsonable_encoder

    return jsonable_encoder(value)


def publish(db: Session, cycle_id: int) -> int:
    """Computes and stores every summary for this cycle. Caller commits."""
    from app import panel_adherence, pipeline
    from app.analytics import measures, outcomes, queries
    from app.features.population import load_population

    today = clock.get_today(db)
    settings = pipeline.engine_settings(db)
    rows: list[SummarySnapshot] = []

    def add(kind: str, scope_key: str, payload, mark: str | None = None) -> None:
        rows.append(
            SummarySnapshot(
                kind=kind,
                scope_key=scope_key,
                cycle_id=cycle_id,
                calc_version=CALC_VERSION,
                source_mark=mark,
                as_of=today,
                payload=_json(payload),
            )  # fmt: skip
        )

    pop = load_population(db)
    mark = consent_mark(db)
    pts = panel_adherence.points_from_population(pop, settings)
    for hcp_id, hcp in pop.hcps.items():
        add(
            "panel_adherence",
            hcp_id,
            panel_adherence.panel(hcp, pop.hcp_patients.get(hcp_id, set()), pts, settings),
            mark,
        )
    since = outcomes.since_param(None, today)
    rows_by_audience = {a: outcomes.training_rows(db, pop, a) for a in LEARNING_DIMENSIONS}
    for audience, dimensions in LEARNING_DIMENSIONS.items():
        touches = rows_by_audience[audience]
        for segment_by in dimensions:
            for source in LEARNING_SOURCES:
                payload = outcomes.what_worked(
                    db, audience, segment_by, source, since, pop=pop, rows=touches
                )
                add("learning", f"{audience}|{segment_by}|{source}", payload)
    add("like_for_like", "all", queries.like_for_like(db, rows=rows_by_audience["PATIENT"]))
    add("measures", "all", measures.measures(db, today, settings, None))
    db.add_all(rows)
    db.flush()
    return len(rows)


def publish_safely(db: Session, cycle_id: int) -> bool:
    """Publishes and commits; on any failure rolls back and leaves readers computing live."""
    try:
        count = publish(db, cycle_id)
        db.commit()
        log.info("Published %s summaries for cycle %s", count, cycle_id)
        return True
    except Exception:  # noqa: BLE001 - summaries are an optimisation, never a reason to fail
        db.rollback()
        log.exception("Could not publish summaries for cycle %s", cycle_id)
        return False


# --- Readers ------------------------------------------------------------------------------


def panel_for(db: Session, hcp_id: str) -> dict | None:
    return get(db, "panel_adherence", hcp_id, mark=consent_mark(db))


def learning_for(db: Session, audience: str, segment_by: str, source: str) -> dict | None:
    return get(db, "learning", f"{audience}|{segment_by}|{source}")


def like_for_like(db: Session) -> list | None:
    return get(db, "like_for_like", "all")


def measures_all(db: Session) -> dict | None:
    return get(db, "measures", "all")
