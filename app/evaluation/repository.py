"""Persistence for evaluation/ -- `eval_runs`/`eval_case_results` only
(DATABASE_DESIGN.md's "the table's owning module writes it" convention).
Pure data access, same discipline as every other repository.py in this
codebase: one statement per function, ORM rows in/out, no business rules,
no ORM->Pydantic mapping.

Writes directly to `database/` -- the identical, already-established
exception `app.agents.repository`'s own module docstring documents for
`agent_executions` (ingestion, agents, retrieval each hold the same direct
`database/` dependency for their own owned tables; `app.database`'s own
"database is a leaf module" import-linter contract only forbids `database`
itself from importing back up into `core`/`agents`/`mcp`/`ingestion`/
`retrieval` -- it does not, and never has, forbidden the reverse direction).
Flagged here rather than re-litigated, per that same precedent.

Style matches `app.agents.repository` exactly (`EvalRun` mirrors
`AgentExecution`'s own running->succeeded/failed lifecycle, per
`evaluation_models.py`'s own docstring): ORM `session.add`/`session.get`,
`.flush()` + `.refresh()`, never `.commit()` -- the caller's
`session_scope()`/`get_db_session` commits.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.evaluation_models import EvalCaseResult, EvalRun


async def insert_eval_run(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    model_used: str,
    git_commit: str | None = None,
) -> EvalRun:
    """Create one `eval_runs` row (`status="running"`) and return it with
    server-side defaults (`id`, `started_at`) populated -- the harness-run
    equivalent of `agents.repository.insert_agent_execution`.
    """
    row = EvalRun(
        organization_id=organization_id,
        model_used=model_used,
        git_commit=git_commit,
        status="running",
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def update_eval_run(
    session: AsyncSession, eval_run_id: uuid.UUID, **fields: Any
) -> EvalRun | None:
    """Apply `fields` to an `eval_runs` row, returning the updated row or
    `None` if it doesn't exist. Generic, dict-driven updater -- same
    rationale as `agents.repository.update_agent_execution`: a run has
    enough independently-updatable fields (`status`, `case_count`,
    `passed_count`, the four `avg_*`/`hallucination_count` aggregates,
    `error_detail`, `completed_at`) that a narrow function per field would
    multiply faster than it's worth.
    """
    row = await session.get(EvalRun, eval_run_id)
    if row is None:
        return None
    for key, value in fields.items():
        setattr(row, key, value)
    await session.flush()
    await session.refresh(row)
    return row


async def insert_eval_case_result(
    session: AsyncSession,
    *,
    organization_id: uuid.UUID,
    eval_run_id: uuid.UUID,
    case_id: str,
    question: str,
    route_taken: str | None,
    confidence_score: float | None,
    citation_count: int,
    expected_sources: list[str],
    actual_sources: list[str],
    relevance_score: int | None,
    citation_accuracy_score: int | None,
    completeness_score: int | None,
    grounded: bool | None,
    hallucination_flag: bool,
    passed: bool,
    judge_reasoning: str | None,
    error_detail: str | None,
) -> EvalCaseResult:
    """Create one `eval_case_results` row -- no update counterpart, unlike
    `EvalRun`: a case result is written exactly once, after that golden
    case has fully run (successfully or not), never revised in place --
    see `evaluation_models.EvalCaseResult`'s own docstring on why a failed
    case still gets a written row (`passed=False`, every score `NULL`)
    rather than being silently skipped.
    """
    row = EvalCaseResult(
        organization_id=organization_id,
        eval_run_id=eval_run_id,
        case_id=case_id,
        question=question,
        route_taken=route_taken,
        confidence_score=confidence_score,
        citation_count=citation_count,
        expected_sources=expected_sources,
        actual_sources=actual_sources,
        relevance_score=relevance_score,
        citation_accuracy_score=citation_accuracy_score,
        completeness_score=completeness_score,
        grounded=grounded,
        hallucination_flag=hallucination_flag,
        passed=passed,
        judge_reasoning=judge_reasoning,
        error_detail=error_detail,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row


async def list_recent_eval_runs(
    session: AsyncSession, organization_id: uuid.UUID, *, limit: int = 20
) -> list[EvalRun]:
    """Most recent `eval_runs` for `organization_id`, newest first -- the
    "trend storage" read side Phase 1's roadmap entry calls for ("surface a
    trend line"): a future dashboard/REST endpoint (out of this level's
    scope -- see Phase 2's observability items) queries this the same way
    `agents.repository.get_agent_execution_stats` backs the existing
    Milestone 10 dashboard. `scripts/run_evaluation.py` also calls this
    itself, to print the last few runs' pass rates alongside the one that
    just completed, for at least a minimal trend view without a dashboard.
    """
    stmt = (
        select(EvalRun)
        .where(EvalRun.organization_id == organization_id)
        .order_by(EvalRun.started_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())
