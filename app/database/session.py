"""Async SQLAlchemy engine, session management, and declarative base.

Owned by: database/ (ARCHITECTURE.md section 3 -- infrastructure layer, no
business logic, sits below every other module and can call nothing).

Every module that needs a DB session gets one through `get_db_session`
(the FastAPI dependency) or `session_scope` (for non-request contexts, e.g.
arq worker jobs) -- nothing outside this file constructs a session directly.
This is what keeps session lifecycle (commit/rollback/close) consistent
regardless of which module is doing the querying.

Cross-module discipline reminder (ARCHITECTURE.md section 2): a session
created here must never be passed across a module's public interface boundary
-- e.g. `core.get_incident(...)` returns a Pydantic model, not an ORM object
still attached to a session. That rule is enforced by convention in the
modules that use this file, not by anything in this file itself.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.shared.config.settings import get_settings

# The Postgres GUC every Milestone 10 RLS policy checks against (see
# app/database/migrations/versions/c7d4e8f19a2b_milestone_10_row_level_security.py).
# Named here, not just there, since this module is the one place that ever
# sets it.
_TENANT_GUC_NAME = "app.current_organization_id"

# Query parameters libpq-style clients (psql, psycopg) understand but
# asyncpg's connect() does not -- SQLAlchemy's asyncpg dialect forwards every
# URL query parameter straight through as a keyword argument to
# asyncpg.connect(), so an unrecognized one raises a bare
# `TypeError: connect() got an unexpected keyword argument '...'` rather than
# a clear connection error. Neon's default copy-paste connection string
# includes both of these by default. SSL is still enforced -- via
# `connect_args` in `_build_engine`, using the parameter name asyncpg
# actually accepts, not via a query string it doesn't understand.
_UNSUPPORTED_ASYNCPG_QUERY_PARAMS = {"sslmode", "channel_binding"}


def _normalize_database_url(raw_url: str) -> str:
    """Strip libpq-only query parameters from a connection string before
    handing it to asyncpg.

    A permanent fix rather than asking every developer to hand-edit their
    `DATABASE_URL` to remove them: a connection string copy-pasted directly
    from Neon's dashboard should just work, unmodified.
    """
    parsed = urlsplit(raw_url)
    kept_params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _UNSUPPORTED_ASYNCPG_QUERY_PARAMS
    ]
    return urlunsplit(parsed._replace(query=urlencode(kept_params)))


class Base(DeclarativeBase):
    """Declarative base for every ORM model in app/database/models/.

    A single shared Base (rather than one per owning module) is deliberate:
    it's what lets Alembic's autogenerate see the entire schema in one
    metadata object, even though DATABASE_DESIGN.md's ownership rules keep
    each table's *write access* scoped to one module by convention.
    """


def _build_engine() -> AsyncEngine:
    """Create the async engine from settings.

    A function rather than a module-level constant so tests can call it
    again after monkeypatching `Settings.database_url` (via
    `get_settings.cache_clear()`), consistent with the pattern already
    established in settings.py.
    """
    settings = get_settings()
    return create_async_engine(
        _normalize_database_url(str(settings.database_url)),
        echo=settings.environment == "development",
        pool_pre_ping=True,
        # Neon requires SSL; asyncpg wants it as a connect-time keyword, not
        # a URL query parameter (see _normalize_database_url above).
        # NOTE: unconditional today because Neon is the only target this
        # project connects to so far (DATABASE_DESIGN.md). Once local
        # Postgres (docker/docker-compose.yml, not yet created) is wired up
        # for development, this will need to become conditional -- a local,
        # non-SSL Postgres would fail to connect with `ssl=True` forced on.
        connect_args={"ssl": True},
    )


engine: AsyncEngine = _build_engine()

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a session, commits on success, rolls back
    and re-raises on exception, always closes.

    Usage in a module's repository.py:
        async def get_incident(session: AsyncSession = Depends(get_db_session)):
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def set_tenant_context(session: AsyncSession, organization_id: uuid.UUID) -> None:
    """Set the session-local GUC every Milestone 10 RLS policy checks
    (`app.current_organization_id`) for the remainder of the *current
    transaction only*.

    Uses `set_config(name, value, is_local=true)` rather than a literal
    `SET LOCAL app.current_organization_id = '<uuid>'` string -- `SET` is not
    a regular SQL statement and does not accept bind parameters, so building
    it via string interpolation would be the one place in this codebase
    doing that; `set_config` is an ordinary function call and takes a normal
    bound parameter instead. The third argument, `true`, is what makes this
    "local" (scoped to the current transaction, cleared on commit/rollback)
    rather than "session" (would otherwise leak forward to whatever the next
    request/job on this pooled connection happens to be, since sessions
    aren't guaranteed to map 1:1 to connections).

    Callers, in the order Identity/org context becomes known in this
    codebase: `app.api.deps.get_current_identity` (REST, every request),
    `app.mcp.auth.resolve_mcp_identity` (MCP, every call),
    `app.core.tenancy.service.evaluate_provisioning` and
    `get_organization_sso_config` (pre-Identity, login-flow paths that
    resolve `organization_id` themselves before any token exists), and
    `app.agents.workers.tasks`'s scheduled Knowledge Gap scan (each
    per-organization iteration, using the `organization_id` the scan loop
    already holds). `app.ingestion.service._execute_ingestion_job` is the
    one exception that cannot call this first -- seeing this module's own
    docstring and that function's docstring for why it resolves the org id
    via a narrow RLS-bypassing lookup before it can call this at all.

    Must be called on every session before any RLS-protected table is
    queried on it -- a session that never calls this sees zero rows from
    every such table (fail-closed; see the RLS migration's own docstring).
    """
    await session.execute(
        text("SELECT set_config(:guc_name, :org_id, true)"),
        {"guc_name": _TENANT_GUC_NAME, "org_id": str(organization_id)},
    )


async def get_current_role_attributes(
    session: "AsyncSession | AsyncConnection",
) -> dict[str, bool]:
    """Return the connected role's `rolsuper`/`rolbypassrls` attributes --
    the two Postgres role attributes that make every Milestone 10 RLS policy
    a silent no-op regardless of `FORCE ROW LEVEL SECURITY` (see the RLS
    migration's own docstring's superuser warning, and Postgres's rule that
    both attributes override `FORCE` unconditionally, checked before the
    policy is even consulted).

    2026-08 audit "C2": this project's `DATABASE_URL` has, in every
    environment observed so far, connected as `neondb_owner` -- Neon's
    default database-owner role -- which `FORCE ROW LEVEL SECURITY` alone
    does not neutralize unless that role also happens to carry `BYPASSRLS`.
    This function is what `scripts/verify_rls_enforcement.py` calls to check
    the *actually-configured* connection's role directly, rather than
    inferring an answer from the connection string's username. See
    `app.database.migrations.versions.f4a7c2e9b3d1_provision_rls_respecting_app_role`
    for the dedicated, non-superuser/non-bypassrls role this project
    provisions once this is confirmed.
    """
    result = await session.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    )
    row = result.one()
    return {"rolsuper": bool(row.rolsuper), "rolbypassrls": bool(row.rolbypassrls)}


@asynccontextmanager
async def session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Context-manager equivalent of `get_db_session` for non-request
    contexts -- arq job handlers (ENGINEERING_DECISIONS.md #002), the
    Knowledge Gap Agent's scheduled graph, CLI scripts, etc. -- anywhere
    FastAPI's `Depends` isn't available.

    Usage:
        async with session_scope() as session:
            ...
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
