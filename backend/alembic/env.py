from logging.config import fileConfig

from alembic import context
from app import models  # noqa: F401  (registers all tables on Base.metadata)
from app.core.config import get_settings
from app.core.db import Base, make_engine

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    # `alembic -x url=...` overrides the app setting (used to check Postgres portability).
    return context.get_x_argument(as_dictionary=True).get("url") or get_settings().database_url


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
    connectable = make_engine(database_url())
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
