"""Alembic migration environment.

Owned by: database/ (ARCHITECTURE.md section 3 -- infrastructure layer).

Wires Alembic to this project's async engine and declarative Base, so:
  - `alembic revision --autogenerate` diffs against `Base.metadata`, which
    will include every model in app/database/models/ once those exist
    (they all import and register against the same Base from session.py).
  - `alembic upgrade` / `downgrade` run through the same async engine
    machinery as the rest of the app, using Settings.database_url --
    not a second, hand-maintained sync connection string.

This file is generated once by `alembic init -t async migrations` and then
hand-edited to point at the project's Base/settings; it is not meant to be
regenerated after this point, only edited as needed (e.g. if new model
modules are added and need to be imported below so their tables are visible
to autogenerate)...
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database.session import Base, engine
from app.shared.config.settings import get_settings

# Import every model module here so its tables register on Base.metadata
# before autogenerate runs. Uncommented as each module is implemented --
# empty for now since no model files exist yet (DATABASE_DESIGN.md tables
# are still just documentation at this point).
#
# from app.database.models import core_models  # noqa: F401
# from app.database.models import agent_models  # noqa: F401
# from app.database.models import mcp_models  # noqa: F401
# from app.database.models import ingestion_models  # noqa: F401
# from app.database.models import retrieval_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Overrides whatever sqlalchemy.url is in alembic.ini with the live setting,
# so there's exactly one source of truth for the connection string
# (Settings.database_url) instead of it being duplicated in two config files.
config.set_main_option("sqlalchemy.url", str(get_settings().database_url))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL scripts without a live DB connection (`--sql` mode)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live DB connection using the async engine."""
    connectable: AsyncEngine = engine

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())