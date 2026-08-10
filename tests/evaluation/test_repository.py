"""Unit tests for `app.evaluation.repository` against a fake `AsyncSession`
that mimics SQLAlchemy ORM's `add`/`get`/`flush`/`refresh` semantics --
no real Postgres involved. Mirrors `tests/ingestion/test_repository.py`'s
"fake session class + assert on captured calls" shape, adapted for
`app.agents.repository`'s ORM style (`session.add`/`session.get`) rather
than that module's raw-SQL `session.execute(text(...))` style, since
`app.evaluation.repository` follows the former, not the latter.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.database.models.evaluation_models import EvalCaseResult, EvalRun
from app.evaluation import repository


class _FakeSession:
    """`add` registers a row; `flush` simulates the server-side defaults
    (`id`, `started_at`/`created_at`) a real Postgres flush would populate;
    `refresh` is a no-op (the fake row is already "current"); `get` returns
    whatever `get_return_value` is set to, recording the (model, pk) it was
    called with.
    """

    def __init__(self) -> None:
        self.added: list[object] = []
        self.flush_count = 0
        self.refreshed: list[object] = []
        self.get_calls: list[tuple[type, uuid.UUID]] = []
        self.get_return_value: object | None = None

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        self.flush_count += 1
        for row in self.added:
            if getattr(row, "id", None) is None:
                row.id = uuid.uuid4()  # type: ignore[attr-defined]
            if isinstance(row, EvalRun) and row.started_at is None:
                row.started_at = datetime.now(timezone.utc)
            if isinstance(row, EvalCaseResult) and row.created_at is None:
                row.created_at = datetime.now(timezone.utc)

    async def refresh(self, row: object) -> None:
        self.refreshed.append(row)

    async def get(self, model: type, pk: uuid.UUID) -> object | None:
        self.get_calls.append((model, pk))
        return self.get_return_value


@pytest.mark.asyncio
async def test_insert_eval_run_sets_running_status_and_flushes() -> None:
    session = _FakeSession()
    organization_id = uuid.uuid4()

    row = await repository.insert_eval_run(
        session, organization_id=organization_id, model_used="gpt-4o-mini", git_commit="abc123"
    )

    assert row.organization_id == organization_id
    assert row.model_used == "gpt-4o-mini"
    assert row.git_commit == "abc123"
    assert row.status == "running"
    assert row.id is not None
    assert session.added == [row]
    assert session.flush_count == 1
    assert session.refreshed == [row]


@pytest.mark.asyncio
async def test_insert_eval_run_defaults_git_commit_to_none() -> None:
    session = _FakeSession()
    row = await repository.insert_eval_run(
        session, organization_id=uuid.uuid4(), model_used="gpt-4o-mini"
    )
    assert row.git_commit is None


@pytest.mark.asyncio
async def test_update_eval_run_applies_fields_and_returns_row() -> None:
    session = _FakeSession()
    existing = EvalRun(
        id=uuid.uuid4(), organization_id=uuid.uuid4(), model_used="gpt-4o-mini", status="running"
    )
    session.get_return_value = existing

    updated = await repository.update_eval_run(
        session, existing.id, status="succeeded", case_count=12, passed_count=10
    )

    assert updated is existing
    assert updated.status == "succeeded"
    assert updated.case_count == 12
    assert updated.passed_count == 10
    assert session.get_calls == [(EvalRun, existing.id)]
    assert session.flush_count == 1
    assert session.refreshed == [existing]


@pytest.mark.asyncio
async def test_update_eval_run_returns_none_when_row_missing() -> None:
    session = _FakeSession()
    session.get_return_value = None

    result = await repository.update_eval_run(session, uuid.uuid4(), status="failed")

    assert result is None
    assert session.flush_count == 0


@pytest.mark.asyncio
async def test_insert_eval_case_result_persists_every_field() -> None:
    session = _FakeSession()
    organization_id = uuid.uuid4()
    eval_run_id = uuid.uuid4()

    row = await repository.insert_eval_case_result(
        session,
        organization_id=organization_id,
        eval_run_id=eval_run_id,
        case_id="checkout-500-runbook",
        question="How do we usually handle checkout service 500 errors?",
        route_taken="answer",
        confidence_score=0.87,
        citation_count=2,
        expected_sources=["runbooks", "slack"],
        actual_sources=["runbooks"],
        relevance_score=8,
        citation_accuracy_score=7,
        completeness_score=6,
        grounded=True,
        hallucination_flag=False,
        passed=True,
        judge_reasoning="Directly supported by the runbook.",
        error_detail=None,
    )

    assert row.organization_id == organization_id
    assert row.eval_run_id == eval_run_id
    assert row.case_id == "checkout-500-runbook"
    assert row.expected_sources == ["runbooks", "slack"]
    assert row.actual_sources == ["runbooks"]
    assert row.passed is True
    assert row.id is not None
    assert session.flush_count == 1


@pytest.mark.asyncio
async def test_insert_eval_case_result_allows_all_null_scores_for_a_failed_case() -> None:
    """Regression: `EvalCaseResult`'s own docstring documents that a case
    which failed before the judge ever ran must still be writable with
    every score column `None` -- this must not raise.
    """
    session = _FakeSession()

    row = await repository.insert_eval_case_result(
        session,
        organization_id=uuid.uuid4(),
        eval_run_id=uuid.uuid4(),
        case_id="broken-case",
        question="A question that blew up.",
        route_taken=None,
        confidence_score=None,
        citation_count=0,
        expected_sources=[],
        actual_sources=[],
        relevance_score=None,
        citation_accuracy_score=None,
        completeness_score=None,
        grounded=None,
        hallucination_flag=False,
        passed=False,
        judge_reasoning=None,
        error_detail="answer_question raised: boom",
    )

    assert row.passed is False
    assert row.relevance_score is None
    assert row.error_detail == "answer_question raised: boom"


@pytest.mark.asyncio
async def test_list_recent_eval_runs_queries_by_organization_and_limit(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    expected_rows = [EvalRun(id=uuid.uuid4(), organization_id=organization_id, model_used="m")]

    class _FakeScalars:
        def all(self) -> list[EvalRun]:
            return expected_rows

    class _FakeResult:
        def scalars(self) -> _FakeScalars:
            return _FakeScalars()

    class _QuerySession:
        def __init__(self) -> None:
            self.executed_statement: object | None = None

        async def execute(self, statement: object) -> _FakeResult:
            self.executed_statement = statement
            return _FakeResult()

    session = _QuerySession()

    result = await repository.list_recent_eval_runs(session, organization_id, limit=5)  # type: ignore[arg-type]

    assert result == expected_rows
    assert session.executed_statement is not None
