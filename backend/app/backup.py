"""Backups that can be restored, for both supported databases.

    python -m app.backup create [TARGET]     back up the configured database (NBA_DATABASE_URL)
    python -m app.backup verify FILE         prove a backup restores: integrity and row counts
    python -m app.backup verify FILE --into postgresql+psycopg://user@host/scratch_db

SQLite: `create` uses SQLite's online backup API, which copies a consistent snapshot while
the application keeps running (copying the .db file of a live database is not safe: the
write-ahead log may hold committed pages). `verify` opens the copy read-only, runs
`PRAGMA integrity_check` and counts the rows of every table.

PostgreSQL: `create` runs `pg_dump --format=custom`; `verify` restores it with `pg_restore`
into a separate, explicitly named database given with --into (it must be empty and must not
be the configured application database) and counts the rows there. Credentials come from
the usual libpq sources (pgpass file, PGPASSWORD); they are never printed or written here.

Nothing here ever writes to the application database.
"""

import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, inspect, make_url, select, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.core.config import REPO_ROOT, get_settings

BACKUP_DIR = REPO_ROOT / "backups"


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _pg_args(url: URL) -> list[str]:
    """libpq connection arguments without the password (pgpass / PGPASSWORD supply it)."""
    args = ["--host", url.host or "localhost", "--port", str(url.port or 5432)]
    if url.username:
        args += ["--username", url.username]
    return [*args, "--no-password"]


def _pg_tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for root in sorted(Path("C:/Program Files/PostgreSQL").glob("*/bin"), reverse=True):
        candidate = root / f"{name}.exe"
        if candidate.exists():
            return str(candidate)
    raise SystemExit(f"{name} not found: install the PostgreSQL client tools or add them to PATH")


def create(database_url: str, target: Path | None = None) -> Path:
    url = make_url(database_url)
    BACKUP_DIR.mkdir(exist_ok=True)
    if url.get_backend_name() == "sqlite":
        source = Path(url.database)
        target = target or BACKUP_DIR / f"{source.stem}-{_stamp()}.db"
        with (
            sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src,
            sqlite3.connect(target) as dst,
        ):
            src.backup(dst)
        return target
    if url.get_backend_name() == "postgresql":
        target = target or BACKUP_DIR / f"{url.database}-{_stamp()}.dump"
        subprocess.run(
            [_pg_tool("pg_dump"), *_pg_args(url), "--format=custom", "--file", str(target),
             url.database],
            check=True,
        )  # fmt: skip
        return target
    raise SystemExit(f"Unsupported database: {url.get_backend_name()}")


def row_counts(engine) -> dict[str, int]:
    names = sorted(inspect(engine).get_table_names())
    with engine.connect() as conn:
        return {
            n: conn.execute(select(text("count(*)")).select_from(text(f'"{n}"'))).scalar_one()
            for n in names
        }


def verify_sqlite(file: Path) -> dict:
    with sqlite3.connect(f"file:{file}?mode=ro", uri=True) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    engine = create_engine(f"sqlite:///file:{file}?mode=ro&uri=true", poolclass=NullPool)
    counts = row_counts(engine)
    engine.dispose()
    return {"integrity": integrity, "tables": len(counts), "rows": counts}


def verify_postgres(file: Path, into: str) -> dict:
    target = make_url(into)
    configured = make_url(get_settings().database_url)
    if (target.host, target.port, target.database) == (
        configured.host,
        configured.port,
        configured.database,
    ):
        raise SystemExit("Refusing to restore into the configured application database")
    engine = create_engine(into, poolclass=NullPool)
    if inspect(engine).get_table_names():
        raise SystemExit(f"Restore target {target.database} is not empty")
    subprocess.run(
        [_pg_tool("pg_restore"), *_pg_args(target), "--no-owner", "--dbname", target.database,
         str(file)],
        check=True,
    )  # fmt: skip
    counts = row_counts(engine)
    engine.dispose()
    return {"integrity": "restored", "tables": len(counts), "rows": counts}


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in ("create", "verify"):
        print(__doc__)
        return 2
    if args[0] == "create":
        target = Path(args[1]) if len(args) > 1 else None
        path = create(get_settings().database_url, target)
        print(f"Backup written: {path}")
        return 0
    file = Path(args[1])
    if "--into" in args:
        result = verify_postgres(file, args[args.index("--into") + 1])
    else:
        result = verify_sqlite(file)
    print(f"Integrity: {result['integrity']}; tables: {result['tables']}; "
          f"rows: {sum(result['rows'].values())}")  # fmt: skip
    return 0 if result["integrity"] in ("ok", "restored") else 1


if __name__ == "__main__":
    sys.exit(main())
