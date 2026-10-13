from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.engine import Engine
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


DATA_DIR.mkdir(exist_ok=True)
engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
