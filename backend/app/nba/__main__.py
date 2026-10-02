"""Run one engine cycle:  python -m app.nba [target ids to print ...]"""

import sys
from collections import Counter

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import Nba
from app.nba.engine import run_cycle

DEFAULT_SHOW = ["PAT_00001", "PAT_00002", "PAT_00004", "PAT_00005", "PAT_00006"] + [
    "HCP_0001",
    "HCP_0002",
    "HCP_0003",
]


def main() -> int:
    show = sys.argv[1:] or DEFAULT_SHOW
    with SessionLocal() as db:
        result = run_cycle(db)
        db.commit()
        print(f"Cycle {result.cycle.id} for {result.cycle.as_of_date}")
        for key, value in result.stats.items():
            print(f"  {key}: {value}")
        mix = Counter(
            (n.target_type, n.status, n.action, n.channel)
            for n in db.scalars(select(Nba).where(Nba.cycle_id == result.cycle.id))
        )
        print("\nMix (target, status, action, channel)")
        for key, n in sorted(mix.items(), key=lambda kv: -kv[1])[:18]:
            print(f"  {n:>5}  {' / '.join(key)}")
        for target_id in show:
            nba = db.scalar(
                select(Nba)
                .where(Nba.target_id == target_id, Nba.cycle_id == result.cycle.id)
                .order_by(Nba.id.desc())
            )
            print(f"\n{target_id}")
            if nba is None:
                print("  no recommendation this cycle")
                continue
            print(
                f"  [{nba.status}] {nba.action} via {nba.channel}, content {nba.content_id}, "
                f"score {nba.score}, priority {nba.priority}"
            )
            if nba.block_reason:
                print(f"  blocked: {nba.block_reason}")
            print(f"  {nba.rationale}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
