from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import MetaData, create_engine, event, inspect
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import DATA_DIR, get_settings

# Explicit constraint names keep Alembic migrations portable between SQLite and Postgres.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


SQLITE_BUSY_MS = 15_000


def make_engine(url: str, **kwargs) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    connect_args.update(kwargs.pop("connect_args", {}))
    engine = create_engine(url, connect_args=connect_args, **kwargs)
    if url.startswith("sqlite"):
        file_backed = engine.url.database not in (None, "", ":memory:")

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            if file_backed:
                # Readers no longer wait for a writer (write-ahead log), and a writer waits
                # up to SQLITE_BUSY_MS for another writer instead of failing at once. The web
                # server and the worker share one file. SQLite only: never sent to PostgreSQL.
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
            cursor.close()

    return engine


class MissingDatabase(RuntimeError):
    """The configured database file does not exist. Never replaced by a new, empty one."""


def sqlite_file(url: str) -> Path | None:
    """The file behind a SQLite URL, or None (in-memory SQLite, or another database)."""
    u = make_url(url)
    if u.get_backend_name() != "sqlite" or not u.database or u.database == ":memory:":
        return None
    return Path(u.database)


def require_existing(url: str) -> None:
    """Refuses to go on if the configured SQLite file is missing: SQLite would otherwise create
    an empty database there and the app would run on it without a word."""
    path = sqlite_file(url)
    if path is not None and not path.exists():
        raise MissingDatabase(
            f"Database file not found: {path}. Nothing was created. Check NBA_DATABASE_PROFILE "
            "(traditional or ai) or NBA_DATABASE_URL; a new database is made only on purpose, "
            "with `alembic upgrade head` and `python -m app.datagen`."
        )


def _inspector(db_or_bind):
    """Inspect through the session's own connection, inside its transaction. Inspecting the
    engine instead can hand back (and then reset) the same pooled connection, which would roll
    back the session's pending changes."""
    return inspect(db_or_bind.connection() if isinstance(db_or_bind, Session) else db_or_bind)


def present_tables(db_or_bind, tables=None) -> list:
    """The model tables (default: all, in dependency order) that exist in this database.

    A database may lack tables of an optional feature (the traditional database has no AI
    tables); loops over every model table use this so they never touch a missing one."""
    names = set(_inspector(db_or_bind).get_table_names())
    return [
        t
        for t in (tables if tables is not None else Base.metadata.sorted_tables)
        if t.name in names
    ]


def has_table(db_or_bind, name: str) -> bool:
    return _inspector(db_or_bind).has_table(name)


DATA_DIR.mkdir(exist_ok=True)
engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
