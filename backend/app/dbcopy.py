"""Copy every row from one database to another (for example SQLite to PostgreSQL), then prove
the copy: row counts and primary keys match table by table, and id sequences continue after
the highest copied id.

    python -m app.dbcopy SOURCE_URL TARGET_URL

The target must already be migrated to the same Alembic revision (`alembic upgrade head`) and
must hold no rows: the tool refuses anything else and never deletes or overwrites data. The
source is only read. Use it for rehearsals against disposable databases and, later, for the
real move once a hosting decision is made.
"""

import sys

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401  (registers every table on Base.metadata)
from app.core.db import Base

BATCH = 2000


def _revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        if "alembic_version" not in inspect(engine).get_table_names():
            return None
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def copy(source: Engine, target: Engine) -> dict[str, int]:
    if _revision(source) != _revision(target):
        raise SystemExit("Source and target are not at the same Alembic revision")
    tables = Base.metadata.sorted_tables
    with target.connect() as conn:
        occupied = [
            t.name for t in tables if conn.execute(select(func.count()).select_from(t)).scalar()
        ]
    if occupied:
        raise SystemExit(f"Target is not empty ({', '.join(occupied[:5])}...): refusing to copy")
    counts: dict[str, int] = {}
    with source.connect() as src, target.begin() as dst:
        for table in tables:
            result = src.execute(select(table))
            total = 0
            while rows := result.mappings().fetchmany(BATCH):
                dst.execute(table.insert(), [dict(r) for r in rows])
                total += len(rows)
            counts[table.name] = total
        if target.dialect.name == "postgresql":
            # Sequences continue after the highest copied id, so the next insert never collides.
            for table in tables:
                pk = list(table.primary_key.columns)
                if (
                    len(pk) == 1
                    and pk[0].autoincrement in (True, "auto")
                    and pk[0].type.python_type is int
                ):
                    name, column = table.name, pk[0].name
                    sequence = f"pg_get_serial_sequence('\"{name}\"', '{column}')"
                    highest = f'COALESCE((SELECT MAX("{column}") FROM "{name}"), 0) + 1'
                    dst.execute(text(f"SELECT setval({sequence}, {highest}, false)"))
    return counts


def compare(source: Engine, target: Engine) -> list[str]:
    """Differences in row counts or primary keys per table (empty when identical)."""
    problems = []
    with source.connect() as a, target.connect() as b:
        for table in Base.metadata.sorted_tables:
            pk = list(table.primary_key.columns)
            left = set(a.execute(select(*pk)).all())
            right = set(b.execute(select(*pk)).all())
            if left != right:
                problems.append(
                    f"{table.name}: {len(left)} rows vs {len(right)}, "
                    f"{len(left - right)} missing, {len(right - left)} extra"
                )
    return problems


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    source = create_engine(sys.argv[1], poolclass=NullPool)
    target = create_engine(sys.argv[2], poolclass=NullPool)
    counts = copy(source, target)
    problems = compare(source, target)
    print(f"Copied {sum(counts.values())} rows in {len(counts)} tables")
    for p in problems:
        print("  MISMATCH", p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
