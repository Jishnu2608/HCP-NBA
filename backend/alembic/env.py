from logging.config import fileConfig

from sqlalchemy import create_engine

from alembic import context
from app import models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.config import DATABASE_FILES, get_settings
from app.core.db import Base, sqlite_file

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    # `alembic -x url=...` overrides the app setting (used to check Postgres portability).
    return context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url


# Commands that change the schema or its recorded revision.
CHANGING = {"upgrade", "downgrade", "stamp"}


def guard_traditional(url: str) -> None:
    """Schema changes go to the AI database (`NBA_DATABASE_PROFILE=ai`), never to the existing
    traditional database by accident. Override only on purpose: `-x allow_traditional=yes`.
    Reading commands (current, history, check) are not affected; neither is creating a new
    traditional database from scratch on a fresh clone (the file does not exist yet)."""
    cmd = getattr(config.cmd_opts, "cmd", None)
    name = getattr(cmd[0], "__name__", "") if cmd else ""
    if name and name not in CHANGING:
        return
    path = sqlite_file(url)
    traditional = DATABASE_FILES["traditional"]
    if path is None or not path.exists() or path.resolve() != traditional.resolve():
        return
    if context.get_x_argument(as_dictionary=True).get("allow_traditional") == "yes":
        return
    raise SystemExit(
        f"Refusing to change the schema of the traditional database ({traditional}). "
        "Migrate the AI database instead (NBA_DATABASE_PROFILE=ai), or pass "
        "-x allow_traditional=yes if this is really intended (back it up first)."
    )


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A plain engine, not the application's: on SQLite the app turns foreign-key enforcement
    # on per connection, but batch migrations rebuild tables (copy, drop, rename) and that
    # cannot run while other tables hold enforced references to the one being rebuilt.
    guard_traditional(database_url())
    connectable = create_engine(database_url())
    with connectable.connect() as connection:
        # Batch mode lets SQLite handle ALTER-style migrations; harmless on Postgres.
        context.configure(
            connection=connection, target_metadata=target_metadata, render_as_batch=True
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
