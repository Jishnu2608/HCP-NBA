"""One full engine cycle: refresh features, recommend, draft.

Command line:  python -m app.cycle [--retrain]
"""

import sys

from sqlalchemy.orm import Session

from app import pipeline
from app.core.db import SessionLocal
from app.features.population import load_population
from app.llm.service import draft_cycle
from app.nba.engine import CycleResult, run_cycle


def run(db: Session, retrain: bool = False) -> CycleResult:
    """Caller commits."""
    pop = load_population(db)
    features = pipeline.refresh_features(db, pop)
    if retrain:
        pipeline.train_models(db, pop)
    result = run_cycle(db, pop)
    result.stats = {**result.stats, "drafts": draft_cycle(db, result.cycle.id), **features}
    result.cycle.stats = result.stats
    db.flush()
    return result


def run_in_steps(db: Session, retrain: bool = False, progress=lambda _text: None) -> CycleResult:
    """The same cycle as `run`, committed in steps so no write transaction spans the whole
    run (used by the background worker):

    1. features (a refresh for today; repeating it is harmless),
    2. models, if retraining (a new registered version),
    3. the engine: superseded recommendations expire and the new ones are written in one
       transaction, so readers see either the old set or the new one, never half of each,
    4. drafts (the send path drafts on demand from the approved template if a cycle ever
       stopped before this step),
    5. summary snapshots (a failure keeps the previous ones valid only for their own cycle,
       and readers compute live meanwhile).
    """
    progress("Refreshing features")
    pop = load_population(db)
    features = pipeline.refresh_features(db, pop)
    db.commit()
    if retrain:
        progress("Retraining models")
        pipeline.train_models(db, pop)
        db.commit()
    progress("Running the engine")
    result = run_cycle(db, pop)
    result.stats = {**result.stats, **features}
    result.cycle.stats = result.stats
    db.commit()
    progress("Drafting wording")
    result.stats = {**result.stats, "drafts": draft_cycle(db, result.cycle.id)}
    result.cycle.stats = result.stats
    db.commit()
    progress("Publishing summaries")
    from app.analytics import snapshots

    snapshots.publish_safely(db, result.cycle.id)
    return result


def main() -> int:
    with SessionLocal() as db:
        result = run(db, retrain="--retrain" in sys.argv)
        db.commit()
        print(f"Cycle {result.cycle.id} for {result.cycle.as_of_date}")
        for key, value in result.stats.items():
            print(f"  {key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
