"""Seed the database:  python -m app.datagen [--patients N] [--hcps N] [--seed N]"""

import argparse
import sys
import time

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.datagen.generate import GenConfig, generate
from app.datagen.validate import validate


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic demo dataset.")
    parser.add_argument("--patients", type=int, default=3000)
    parser.add_argument("--hcps", type=int, default=300)
    parser.add_argument("--seed", type=int, default=get_settings().datagen_seed)
    args = parser.parse_args()

    started = time.perf_counter()
    with SessionLocal() as db:
        counts = generate(db, GenConfig(seed=args.seed, n_patients=args.patients, n_hcps=args.hcps))
        checks = validate(db)

    print(f"Generated in {time.perf_counter() - started:.1f}s (seed {args.seed})\n")
    for table, n in counts.items():
        if n:
            print(f"  {table:<24}{n:>8,}")
    print("\nValidation")
    for c in checks:
        print(
            f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}"
            + (f"  ({c.detail})" if c.detail else "")
        )
    failed = [c for c in checks if not c.passed]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
