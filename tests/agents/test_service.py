"""Tests for `app.agents.service`'s Milestone 8 additions:
`search_similar_incidents`, `search_recent_changes`, and the latter's
`_passes_recency_filter` best-effort `since` check -- plus Milestone 9's
`detect_knowledge_gaps`/`list_gap_reports`.

Follows the same `monkeypatch.setattr(<module>.<dependency>, ...)` style
already established in `tests/agents/investigation/test_evidence.py` --
patching the shared `app.retrieval.service` module object via the alias
`app.agents.service` imports it under (`retrieval_service`), not a copy of
it, so the patch is visible to `agents_service.search_similar_incidents`/
`search_recent_changes` exactly as it would be in production.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.agents import service as agents_service
from app.agents.service import _passes_recency_filter
from app.core.exceptions import PermissionDeniedError
from app.retrieval.schemas import ScoredChunk, SearchFilters
from app.shared.schemas import ActorKind, Identity


def _reviewer(organization_id: uuid.UUID) -> Identity:
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        permissions=frozenset({"knowledge:review"}),
    )


def _chunk(metadata: dict[str, str] | None = None, content: str = "content") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection="code",
        content=content,
        score=0.8,
        source_offset_start=0,
        source_offset_end=len(content),
        title="a title",
        source_url="https://github.com/acme/widgets/commit/abc123",
        metadata=metadata or {},
    )


@pytest.mark.asyncio
async def test_search_similar_incidents_scopes_filters_to_actor_org(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_search(session, query, filters, top_k, *args, **kwargs):
        captured["session"] = session
        captured["query"] = query
        captured["filters"] = filters
        captured["top_k"] = top_k
        captured["args"] = args
        return [_chunk()]

    monkeypatch.setattr(agents_service.retrieval_service, "search", fake_search)

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await agents_service.search_similar_incidents(None, "checkout failing", actor)

    assert len(result) == 1
    assert isinstance(captured["filters"], SearchFilters)
    assert captured["filters"].organization_id == actor.organization_id
    assert captured["top_k"] == 10
    # No collection is passed -- `retrieval.search`'s own all-collections
    # default (`collection=None`) applies, since no "incidents" collection
    # exists (see the function's own docstring for why).
    assert captured["args"] == ()


@pytest.mark.asyncio
async def test_search_similar_incidents_restricts_to_actor_project_memberships(
    monkeypatch,
) -> None:
    """Confirmed-leak regression test (2026-08 audit "C1"): an actor who
    belongs to Project A only must not be able to pull Project B's evidence
    through `search_similar_incidents`.
    """
    captured: dict[str, object] = {}

    async def fake_search(session, query, filters, top_k, *args, **kwargs):
        captured["filters"] = filters
        return [_chunk()]

    monkeypatch.setattr(agents_service.retrieval_service, "search", fake_search)

    organization_id = uuid.uuid4()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()  # no membership row -- must never be searched
    actor = Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        project_permissions={project_a: frozenset({"incident:write"})},
    )

    await agents_service.search_similar_incidents(None, "checkout failing", actor)

    filters = captured["filters"]
    assert isinstance(filters, SearchFilters)
    assert filters.project_ids == [project_a]
    assert project_b not in (filters.project_ids or [])


@pytest.mark.asyncio
async def test_search_similar_incidents_without_project_memberships_is_unrestricted(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_search(session, query, filters, top_k, *args, **kwargs):
        captured["filters"] = filters
        return [_chunk()]

    monkeypatch.setattr(agents_service.retrieval_service, "search", fake_search)

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    await agents_service.search_similar_incidents(None, "checkout failing", actor)

    filters = captured["filters"]
    assert isinstance(filters, SearchFilters)
    assert filters.project_ids is None


@pytest.mark.asyncio
async def test_search_recent_changes_restricts_to_actor_project_memberships(monkeypatch) -> None:
    """Same confirmed-leak scenario as `search_similar_incidents`, for
    `search_recent_changes`.
    """
    captured: dict[str, object] = {}

    async def fake_search(session, query, filters, top_k, collection=None, *, include_metadata=False):
        captured["filters"] = filters
        return [_chunk()]

    monkeypatch.setattr(agents_service.retrieval_service, "search", fake_search)

    organization_id = uuid.uuid4()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    actor = Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        project_permissions={project_a: frozenset({"incident:write"})},
    )

    await agents_service.search_recent_changes(None, "checkout", actor)

    filters = captured["filters"]
    assert isinstance(filters, SearchFilters)
    assert filters.project_ids == [project_a]
    assert project_b not in (filters.project_ids or [])


@pytest.mark.asyncio
async def test_search_recent_changes_searches_code_collection_with_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_search(session, query, filters, top_k, collection=None, *, include_metadata=False):
        captured["collection"] = collection
        captured["include_metadata"] = include_metadata
        return [_chunk()]

    monkeypatch.setattr(agents_service.retrieval_service, "search", fake_search)

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await agents_service.search_recent_changes(None, "checkout", actor)

    assert len(result) == 1
    assert captured["collection"] == "code"
    assert captured["include_metadata"] is True


@pytest.mark.asyncio
async def test_search_recent_changes_filters_out_stale_chunks(monkeypatch) -> None:
    since = datetime(2026, 7, 15, tzinfo=timezone.utc)
    fresh = _chunk({"source_timestamp": "2026-07-20T00:00:00Z"})
    stale = _chunk({"source_timestamp": "2026-07-01T00:00:00Z"})
    no_timestamp = _chunk({})

    async def fake_search(*args, **kwargs):
        return [fresh, stale, no_timestamp]

    monkeypatch.setattr(agents_service.retrieval_service, "search", fake_search)

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await agents_service.search_recent_changes(None, "checkout", actor, since=since)

    # `stale` is dropped; `no_timestamp` is kept (see docstring: "no
    # timestamp available" is not the same claim as "not recent").
    assert result == [fresh, no_timestamp]


@pytest.mark.asyncio
async def test_search_recent_changes_returns_everything_without_since(monkeypatch) -> None:
    chunks = [_chunk({"source_timestamp": "2020-01-01T00:00:00Z"}), _chunk({})]

    async def fake_search(*args, **kwargs):
        return chunks

    monkeypatch.setattr(agents_service.retrieval_service, "search", fake_search)

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await agents_service.search_recent_changes(None, "checkout", actor)

    assert result == chunks


def test_passes_recency_filter_keeps_chunk_with_no_recognized_metadata_key() -> None:
    chunk = _chunk({"unrelated_key": "value"})
    assert _passes_recency_filter(chunk, datetime.now(timezone.utc)) is True


def test_passes_recency_filter_handles_zulu_suffix() -> None:
    chunk = _chunk({"timestamp": "2026-08-01T00:00:00Z"})
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert _passes_recency_filter(chunk, since) is True


def test_passes_recency_filter_treats_naive_timestamp_as_utc() -> None:
    chunk = _chunk({"updated_at": "2026-08-01T00:00:00"})
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert _passes_recency_filter(chunk, since) is True

    since_future = datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert _passes_recency_filter(chunk, since_future) is False


def test_passes_recency_filter_ignores_unparseable_value_and_checks_next_key() -> None:
    chunk = _chunk({"source_timestamp": "not-a-date", "updated_at": "2026-08-01T00:00:00Z"})
    since = datetime(2026, 7, 1, tzinfo=timezone.utc)
    assert _passes_recency_filter(chunk, since) is True


class _FakeExecutionRow:
    def __init__(self, execution_id: uuid.UUID) -> None:
        self.id = execution_id


@pytest.mark.asyncio
async def test_detect_knowledge_gaps_records_agent_execution_and_returns_reports(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = Identity.for_agent("knowledge_gap_agent", organization_id)
    execution_id = uuid.uuid4()
    recorded: dict[str, object] = {}

    async def fake_insert_agent_execution(session, **kwargs):
        recorded["insert"] = kwargs
        return _FakeExecutionRow(execution_id)

    async def fake_update_agent_execution(session, exec_id, **kwargs):
        recorded["update"] = {"id": exec_id, **kwargs}

    from app.database.models.agent_models import KnowledgeGapReport

    fake_row = KnowledgeGapReport(
        id=uuid.uuid4(),
        organization_id=organization_id,
        suggested_topic="Checkout reliability",
        topic_embedding=[1.0, 0.0],
        supporting_execution_ids=[str(uuid.uuid4())],
        suggested_action="new_runbook",
        related_document_id=None,
        status="open",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    async def fake_pipeline(session, llm, org_id, **kwargs):
        assert org_id == organization_id
        return [fake_row]

    monkeypatch.setattr(agents_service.repository, "insert_agent_execution", fake_insert_agent_execution)
    monkeypatch.setattr(agents_service.repository, "update_agent_execution", fake_update_agent_execution)
    monkeypatch.setattr(agents_service, "_run_knowledge_gap_pipeline", fake_pipeline)

    reports = await agents_service.detect_knowledge_gaps(None, actor)

    assert len(reports) == 1
    assert reports[0].suggested_topic == "Checkout reliability"
    assert recorded["insert"]["agent_name"] == "detect_knowledge_gaps"
    assert recorded["insert"]["trigger_source"] == "scheduled"
    assert recorded["update"]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_detect_knowledge_gaps_marks_failed_and_reraises_on_error(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = Identity.for_agent("knowledge_gap_agent", organization_id)
    execution_id = uuid.uuid4()
    recorded: dict[str, object] = {}

    async def fake_insert_agent_execution(session, **kwargs):
        return _FakeExecutionRow(execution_id)

    async def fake_update_agent_execution(session, exec_id, **kwargs):
        recorded["update"] = kwargs

    async def failing_pipeline(session, llm, org_id, **kwargs):
        raise RuntimeError("clustering blew up")

    monkeypatch.setattr(agents_service.repository, "insert_agent_execution", fake_insert_agent_execution)
    monkeypatch.setattr(agents_service.repository, "update_agent_execution", fake_update_agent_execution)
    monkeypatch.setattr(agents_service, "_run_knowledge_gap_pipeline", failing_pipeline)

    with pytest.raises(RuntimeError):
        await agents_service.detect_knowledge_gaps(None, actor)

    assert recorded["update"]["status"] == "failed"


@pytest.mark.asyncio
async def test_list_gap_reports_requires_knowledge_review_permission() -> None:
    actor = Identity.for_agent("some_agent", uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        await agents_service.list_gap_reports(None, actor)


@pytest.mark.asyncio
async def test_list_gap_reports_returns_reports_for_reviewer(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)

    from app.database.models.agent_models import KnowledgeGapReport

    fake_row = KnowledgeGapReport(
        id=uuid.uuid4(),
        organization_id=organization_id,
        suggested_topic="Auth token expiry",
        topic_embedding=[0.0, 1.0],
        supporting_execution_ids=[str(uuid.uuid4()), str(uuid.uuid4())],
        suggested_action="update_existing",
        related_document_id=uuid.uuid4(),
        status="open",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    async def fake_list_open_gap_reports(session, org_id):
        assert org_id == organization_id
        return [fake_row]

    monkeypatch.setattr(
        agents_service.knowledge_gap_repository, "list_open_gap_reports", fake_list_open_gap_reports
    )

    reports = await agents_service.list_gap_reports(None, actor)

    assert len(reports) == 1
    assert reports[0].suggested_action == "update_existing"
    assert len(reports[0].supporting_execution_ids) == 2


def _observability_reader(organization_id: uuid.UUID) -> Identity:
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        permissions=frozenset({"observability:read"}),
    )


@pytest.mark.asyncio
async def test_get_agent_execution_stats_requires_permission() -> None:
    actor = Identity.for_agent("some_agent", uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        await agents_service.get_agent_execution_stats(None, actor)


@pytest.mark.asyncio
async def test_get_agent_execution_stats_maps_aggregate_rows(monkeypatch) -> None:
    from types import SimpleNamespace

    organization_id = uuid.uuid4()
    actor = _observability_reader(organization_id)
    rows = [
        SimpleNamespace(
            agent_name="answer_question",
            execution_count=20,
            succeeded_count=18,
            failed_count=2,
            avg_confidence_score=0.72,
            avg_latency_seconds=1.5,
        ),
        SimpleNamespace(
            agent_name="detect_knowledge_gaps",
            execution_count=5,
            succeeded_count=5,
            failed_count=0,
            avg_confidence_score=None,
            avg_latency_seconds=None,
        ),
    ]

    async def fake_get_agent_execution_stats(session, org_id, *, since=None):
        assert org_id == organization_id
        return rows

    monkeypatch.setattr(
        agents_service.repository, "get_agent_execution_stats", fake_get_agent_execution_stats
    )

    result = await agents_service.get_agent_execution_stats(None, actor)

    assert len(result) == 2
    assert result[0].agent_name == "answer_question"
    assert result[0].succeeded_count == 18
    assert result[0].failed_count == 2
    assert result[0].avg_confidence_score == 0.72
    assert result[1].avg_confidence_score is None
    assert result[1].avg_latency_seconds is None
