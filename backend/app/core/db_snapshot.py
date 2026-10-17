"""Isolated copies of a SQLite database for read-only inspection.

Opening a live SQLite file, even "just to read", can change it: a read-write connection may
checkpoint the write-ahead log into the main file on close, and any connection may create the
`-wal` and `-shm` side files. Forensic and audit checks (integrity checks, `alembic check`,
replays, counts) should therefore open a copy, never the persistent file.

`snapshot` copies the main file and its write-ahead log byte for byte, without opening either
with SQLite, so nothing about the source changes and committed pages still in the log are kept.
The copy is then a normal database that may be opened freely.

    python -m app.core.db_snapshot data/nba_demodb_ai.db [DEST_DIR]

prints the path of the copy, for example for `alembic -x url=sqlite:///<copy> check`.
"""

import shutil
import sys
import tempfile
from pathlib import Path


def snapshot(source: Path, dest_dir: Path | None = None) -> Path:
    """Copies `source` (and `source-wal` when present) into `dest_dir` (a new temporary
    directory by default) and returns the copy's path. The source is read as plain bytes only.
    Refuses a missing source and a destination inside the source's own directory."""
    source = Path(source).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    dest = Path(dest_dir).resolve() if dest_dir else Path(tempfile.mkdtemp(prefix="nba_snap_"))
    if dest == source.parent:
        raise ValueError("write the snapshot outside the source database's directory")
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / source.name
    shutil.copyfile(source, target)
    wal = source.with_name(source.name + "-wal")
    if wal.is_file():
        shutil.copyfile(wal, target.with_name(target.name + "-wal"))
    return target


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    dest = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    print(snapshot(Path(sys.argv[1]), dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
