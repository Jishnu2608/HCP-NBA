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
