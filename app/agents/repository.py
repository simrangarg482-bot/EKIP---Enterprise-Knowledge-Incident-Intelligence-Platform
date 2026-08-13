"""Persistence for agents/ -- `agent_executions` only (DATABASE_DESIGN.md's
"agents/ -- owned tables"). Pure data access, same discipline as every other
repository.py in this codebase: one statement per function, ORM rows in/out,
no business rules, no ORM->Pydantic mapping.

Writes directly to `database/` -- an ingestion/agents-parallel exception to
PROJECT_PLAN.md section 9.7's dependency list (`retrieval`, `core`, `shared`
only, `database` unlisted). The same gap already resolved three times this
project (ingestion reading `connector_configs` directly, ingestion writing
its own `ingestion_jobs`/`documents` tables, retrieval writing its own
`<collection>_chunks` tables) for an identical reason: DATABASE_DESIGN.md's
"the table's owning module writes it" convention requires *some* module to
hold a direct `database/` dependency for its own tables, and `agent_executions`
is agents-owned, not core-owned or retrieval-owned. Flagged here rather than
re-litigated, per the precedent already established for the earlier three
cases.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.agent_models import AgentExecution

_SUCCEEDED_CASE = case((AgentExecution.status == "succeeded", 1), else_=0)
_FAILED_CASE = case((AgentExecution.status == "failed", 1), else_=0)
# Only completed executions have a meaningful latency (`completed_at` is
# nullable, populated once a run finishes -- see `AgentExecution`'s own
# docstring on the running->succeeded/failed lifecycle); a still-`running`
# row's "elapsed so far" isn't the same measurement and would skew an
# average latency downward for no good reason, so `func.avg` here only ever
# sees non-null values by construction (the `else_=None` branch), not a
# zero standing in for "unknown."
_LATENCY_SECONDS_CASE = case(
    (
        AgentExecution.completed_at.is_not(None),
        func.extract("epoch", AgentExecution.completed_at - AgentExecution.started_at),
    ),
    else_=None,
)


async def insert_agent_execution(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    agent_name: str,
    trigger_source: str,
    input_summary: dict | None = None,
    user_id: uuid.UUID | None = None,
) -> AgentExecution:
    """Create one `agent_executions` row (`status="running"`) and return it
    with server-side defaults (`id`, `started_at`) populated.

    `user_id` (added alongside `GET /ask/history`) is the human who triggered
    this call over REST, if any -- omitted/`None` for MCP and scheduled
    executions, which have no human user to attribute to.
    """
    row = AgentExecution(
        organization_id=organization_id,
        agent_name=agent_name,
        trigger_source=trigger_source,
        input_summary=input_summary,
        user_id=user_id,
        status="running",
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_agent_execution(
    session: AsyncSession, execution_id: uuid.UUID, **fields: Any
) -> AgentExecution | None:
    """Apply `fields` to an `agent_executions` row, returning the updated
    row or None if it doesn't exist. Generic, dict-driven updater -- same
    rationale as `ingestion.repository.update_ingestion_job`: a run has
    enough independently-updatable fields (`status`, `confidence_score`,
    `error_detail`, `completed_at`) that a narrow function per field would
    multiply faster than it's worth.
    """
    row = await session.get(AgentExecution, execution_id)
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await session.refresh(row)
    return row


async def get_agent_execution_stats(
    session: AsyncSession, organization_id: uuid.UUID, *, since: datetime | None = None
) -> list[Any]:
    """Aggregate `agent_executions` by `agent_name` for `organization_id`:
    count, succeeded/failed counts, average confidence, average latency --
    backs the Milestone 10 observability dashboard (`agents.service.
    get_agent_execution_stats`).

    Returns raw SQLAlchemy `Row` objects, same reasoning as `core.
    observability.repository.get_mcp_tool_stats`: an aggregate query has no
    single ORM row to map back onto, so `service.py` builds
    `AgentExecutionStats` directly from these labeled columns.
    """
    stmt = (
        select(
            AgentExecution.agent_name.label("agent_name"),
            func.count().label("execution_count"),
            func.sum(_SUCCEEDED_CASE).label("succeeded_count"),
            func.sum(_FAILED_CASE).label("failed_count"),
            func.avg(AgentExecution.confidence_score).label("avg_confidence_score"),
            func.avg(_LATENCY_SECONDS_CASE).label("avg_latency_seconds"),
        )
        .where(AgentExecution.organization_id == organization_id)
        .group_by(AgentExecution.agent_name)
    )
    if since is not None:
        stmt = stmt.where(AgentExecution.started_at >= since)

    result = await session.execute(stmt)
    return list(result.all())


async def list_agent_executions_for_user(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    limit: int,
    offset: int,
) -> list[AgentExecution]:
    """Return `user_id`'s own past executions within `organization_id`,
    newest first -- backs `GET /ask/history` (`agents.service.
    get_question_history`). Filtered by both `user_id` and `organization_id`:
    the latter is redundant with RLS (this table's policy already restricts
    every query on this session to the caller's own organization) but stated
    explicitly anyway, the same defense-in-depth precedent `core.users.
    repository`'s resolution queries already set for "don't rely on RLS
    alone to be the only thing enforcing tenant isolation in this query."
    """
    stmt = (
        select(AgentExecution)
        .where(
            AgentExecution.user_id == user_id,
            AgentExecution.organization_id == organization_id,
        )
        .order_by(AgentExecution.started_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
