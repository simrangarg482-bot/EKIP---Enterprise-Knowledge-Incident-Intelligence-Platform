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

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.shared.config.settings import get_settings


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
        str(settings.database_url),
        echo=settings.environment == "development",
        pool_pre_ping=True,
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