"""Tests for `app.agents.investigation.evidence`:
  - `_chunk_to_evidence`'s kind -> source mapping -- the piece that lets the
    Investigation Agent tell a GitHub file chunk apart from a commit/
    pull-request/issue chunk, now that `retrieval.search(...,
    include_metadata=True)` can surface each chunk's `document_metadata`
    (see `ingestion.connectors.github`'s module docstring for the `"kind"`
    metadata key convention this relies on).
  - `_should_augment_with_live_evidence`'s hybrid trigger logic and
    `_gather_live_evidence`'s dispatch-by-connector-source behavior -- the
    live-evidence extension (`agents.investigation.live/`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.investigation import evidence as evidence_module
from app.agents.investigation.evidence import (
    _LIVE_SOURCES,
    _chunk_to_evidence,
    _gather_live_evidence,
    _parse_source_timestamp,
    _should_augment_with_live_evidence,
    gather_evidence,
)
from app.agents.investigation.live.monitoring_live import MonitoringLiveSource
from app.core.tenancy.schemas import ConnectorConfig
from app.retrieval.schemas import ScoredChunk, SearchFilters
from app.shared.schemas import ActorKind, EvidenceItem, Identity


def _chunk(metadata: dict[str, str], content: str = "some content") -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection="code",
        content=content,
        score=0.9,
        source_offset_start=0,
        source_offset_end=len(content),
        title="a title",
        source_url="https://github.com/acme/widgets/commit/abc123",
        metadata=metadata,
    )


def test_file_chunk_with_no_kind_defaults_to_github() -> None:
    chunk = _chunk({"repo": "acme/widgets", "path": "src/app.py", "ref": "main"})

    evidence = _chunk_to_evidence(chunk)

    assert evidence.source == "github"
    assert evidence.metadata == chunk.metadata
    assert evidence.source_timestamp is None  # no "timestamp" key on a file chunk


def test_commit_chunk_maps_to_commit_source() -> None:
    chunk = _chunk(
        {
            "repo": "acme/widgets",
            "kind": "commit",
            "sha": "abc123",
            "author": "Ada Lovelace",
            "timestamp": "2026-07-01T10:00:00Z",
            "changed_files": "src/checkout.py",
        }
    )

    evidence = _chunk_to_evidence(chunk)

    assert evidence.source == "commit"
    assert evidence.metadata["sha"] == "abc123"
    assert evidence.source_timestamp == datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)


def test_pull_request_chunk_maps_to_pull_request_source() -> None:
    chunk = _chunk({"repo": "acme/widgets", "kind": "pull_request", "number": "42"})

    evidence = _chunk_to_evidence(chunk)

    assert evidence.source == "pull_request"


def test_issue_chunk_maps_to_issue_source() -> None:
    chunk = _chunk({"repo": "acme/widgets", "kind": "issue", "number": "7", "labels": "bug"})

    evidence = _chunk_to_evidence(chunk)

    assert evidence.source == "issue"
    assert evidence.metadata["labels"] == "bug"


def test_explicit_source_override_wins_over_kind_metadata() -> None:
    """`_gather_slack_evidence` always passes `source="slack"` explicitly --
    confirms that override takes priority even if `metadata["kind"]` were
    somehow present (it never is for real Slack chunks).
    """
    chunk = _chunk({"kind": "commit"})

    evidence = _chunk_to_evidence(chunk, source="slack")

    assert evidence.source == "slack"


def test_reference_falls_back_to_chunk_id_without_source_url() -> None:
    chunk = _chunk({})
    chunk = chunk.model_copy(update={"source_url": None})

    evidence = _chunk_to_evidence(chunk)

    assert evidence.reference == f"chunk:{chunk.chunk_id}"


def test_summary_is_truncated_and_marked_with_ellipsis() -> None:
    long_content = "x" * 500
    chunk = _chunk({}, content=long_content)

    evidence = _chunk_to_evidence(chunk)

    assert len(evidence.summary) == 303  # 300 chars + "..."
    assert evidence.summary.endswith("...")


def test_parse_source_timestamp_handles_missing_and_malformed_values() -> None:
    assert _parse_source_timestamp(None) is None
    assert _parse_source_timestamp("") is None
    assert _parse_source_timestamp("not-a-timestamp") is None
    assert _parse_source_timestamp("2026-07-01T10:00:00Z") == datetime(
        2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc
    )


# --- _should_augment_with_live_evidence -------------------------------------


def _evidence_item(source_timestamp: datetime | None = None) -> EvidenceItem:
    return EvidenceItem(
        source="github",
        reference="ref",
        summary="summary",
        retrieved_at=datetime.now(timezone.utc),
        source_timestamp=source_timestamp,
    )


def test_should_augment_when_incident_id_present() -> None:
    now = datetime.now(timezone.utc)
    evidence = [_evidence_item(source_timestamp=now) for _ in range(10)]  # plenty, fresh

    assert _should_augment_with_live_evidence(evidence, uuid.uuid4()) is True


def test_should_augment_when_evidence_is_thin() -> None:
    now = datetime.now(timezone.utc)
    evidence = [_evidence_item(source_timestamp=now)]  # below _LIVE_EVIDENCE_MIN_COUNT

    assert _should_augment_with_live_evidence(evidence, None) is True


def test_should_augment_when_evidence_is_stale() -> None:
    stale = datetime.now(timezone.utc) - timedelta(hours=5)
    evidence = [_evidence_item(source_timestamp=stale) for _ in range(5)]

    assert _should_augment_with_live_evidence(evidence, None) is True


def test_should_augment_when_no_evidence_is_timestamped() -> None:
    evidence = [_evidence_item(source_timestamp=None) for _ in range(5)]

    assert _should_augment_with_live_evidence(evidence, None) is True


def test_should_not_augment_when_plenty_of_fresh_evidence_and_no_incident() -> None:
    now = datetime.now(timezone.utc)
    evidence = [_evidence_item(source_timestamp=now) for _ in range(5)]

    assert _should_augment_with_live_evidence(evidence, None) is False


# --- _gather_live_evidence ---------------------------------------------------


def _connector_config(source: str, status: str = "active") -> ConnectorConfig:
    now = datetime.now(timezone.utc)
    return ConnectorConfig(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        project_id=None,
        source=source,
        credential_ref="token-ref",
        config={},
        status=status,
        last_synced_at=None,
        created_at=now,
        updated_at=now,
    )


class _FakeLiveSource:
    def __init__(self, items: list[EvidenceItem]) -> None:
        self._items = items
        self.calls = 0

    async def fetch_live_evidence(self, *, connector_config, query, since, limit):
        self.calls += 1
        return self._items


def test_monitoring_connector_source_resolves_to_monitoring_live_source() -> None:
    """`ConnectorSource.MONITORING`'s whole point: a `connector_config` with
    `source="monitoring"` must actually dispatch to `MonitoringLiveSource`,
    not be silently skipped the way an unregistered source (e.g. `"jira"`)
    already is (`test_gather_live_evidence_dispatches_by_connector_source`
    covers that skip behavior). Uses the real, module-level `_LIVE_SOURCES`
    (not a monkeypatched fake, unlike the dispatch tests below) so this test
    fails if the registration itself is ever accidentally removed.
    """
    assert "monitoring" in _LIVE_SOURCES
    assert isinstance(_LIVE_SOURCES["monitoring"], MonitoringLiveSource)


@pytest.mark.asyncio
async def test_gather_live_evidence_dispatches_monitoring_connector(monkeypatch) -> None:
    """End-to-end confirmation that a `source="monitoring"` connector config
    is no longer silently skipped: `_gather_live_evidence` now looks it up in
    `_LIVE_SOURCES` and calls its `fetch_live_evidence`, exactly like
    `"github"`/`"slack"` already do.
    """
    monitoring_config = _connector_config("monitoring")

    async def fake_list_connectors(session, actor, organization_id):
        return [monitoring_config]

    monkeypatch.setattr(evidence_module.tenancy_service, "list_connectors", fake_list_connectors)

    monitoring_item = EvidenceItem(
        source="monitoring", reference="r3", summary="s3", retrieved_at=datetime.now(timezone.utc)
    )
    fake_monitoring = _FakeLiveSource([monitoring_item])
    monkeypatch.setattr(evidence_module, "_LIVE_SOURCES", {"monitoring": fake_monitoring})

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await _gather_live_evidence(
        session=None, query="disk usage spike", actor=actor, retry_count={}
    )

    assert result == [monitoring_item]
    assert fake_monitoring.calls == 1


@pytest.mark.asyncio
async def test_gather_live_evidence_dispatches_by_connector_source(monkeypatch) -> None:
    """One live source per connector `source`; a connector whose source has
    no registered `LiveEvidenceSource` (here, `"jira"`) is silently skipped
    -- mirroring this module's existing, honestly-empty `_gather_jira_evidence`.
    """
    github_config = _connector_config("github")
    slack_config = _connector_config("slack")
    jira_config = _connector_config("jira")

    async def fake_list_connectors(session, actor, organization_id):
        return [github_config, slack_config, jira_config]

    monkeypatch.setattr(evidence_module.tenancy_service, "list_connectors", fake_list_connectors)

    github_item = EvidenceItem(
        source="commit", reference="r1", summary="s1", retrieved_at=datetime.now(timezone.utc)
    )
    slack_item = EvidenceItem(
        source="slack", reference="r2", summary="s2", retrieved_at=datetime.now(timezone.utc)
    )
    fake_github = _FakeLiveSource([github_item])
    fake_slack = _FakeLiveSource([slack_item])
    monkeypatch.setattr(
        evidence_module, "_LIVE_SOURCES", {"github": fake_github, "slack": fake_slack}
    )

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await _gather_live_evidence(
        session=None, query="checkout failing", actor=actor, retry_count={}
    )

    assert result == [github_item, slack_item]
    assert fake_github.calls == 1
    assert fake_slack.calls == 1


@pytest.mark.asyncio
async def test_gather_live_evidence_skips_inactive_connectors(monkeypatch) -> None:
    inactive_config = _connector_config("github", status="error")

    async def fake_list_connectors(session, actor, organization_id):
        return [inactive_config]

    monkeypatch.setattr(evidence_module.tenancy_service, "list_connectors", fake_list_connectors)
    fake_github = _FakeLiveSource(
        [EvidenceItem(source="commit", reference="r", summary="s", retrieved_at=datetime.now(timezone.utc))]
    )
    monkeypatch.setattr(evidence_module, "_LIVE_SOURCES", {"github": fake_github})

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await _gather_live_evidence(session=None, query="q", actor=actor, retry_count={})

    assert result == []
    assert fake_github.calls == 0


@pytest.mark.asyncio
async def test_gather_live_evidence_returns_empty_when_list_connectors_fails(monkeypatch) -> None:
    async def failing_list_connectors(session, actor, organization_id):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(evidence_module.tenancy_service, "list_connectors", failing_list_connectors)

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await _gather_live_evidence(session=None, query="q", actor=actor, retry_count={})

    assert result == []


# --- gather_evidence: project-level SearchFilters scoping (2026-08 audit "C1") ---


@pytest.mark.asyncio
async def test_gather_evidence_restricts_search_to_actor_project_memberships(monkeypatch) -> None:
    """Confirmed-leak regression test: an actor who belongs to Project A
    only must never have Project B's evidence searched on their behalf,
    regardless of what org-level permissions they also hold.
    """
    organization_id = uuid.uuid4()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()  # the actor has NO membership row for this project
    actor = Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        permissions=frozenset({"observability:read"}),
        project_permissions={project_a: frozenset({"incident:write"})},
    )

    captured_filters: list[SearchFilters] = []

    async def fake_search(session, query, filters, top_k, *args, **kwargs):
        captured_filters.append(filters)
        # Enough chunks to hit `_EVIDENCE_CAP` via code+slack evidence alone,
        # so postmortem/monitoring/live sources (which need their own,
        # unrelated mocks) are never reached.
        return [_chunk({}) for _ in range(5)]

    monkeypatch.setattr(evidence_module.retrieval_service, "search", fake_search)

    await gather_evidence(session=None, query="checkout failing", actor=actor, retry_count={})

    assert captured_filters, "retrieval.service.search was never called"
    for filters in captured_filters:
        assert filters.project_ids == [project_a]
        assert project_b not in (filters.project_ids or [])
        assert filters.permission_codes == frozenset({"observability:read", "incident:write"})


@pytest.mark.asyncio
async def test_gather_evidence_without_project_memberships_is_unrestricted(monkeypatch) -> None:
    """Preserves the pre-existing, common-case behavior: an actor with no
    project-scoped membership rows still searches every project they can
    see via their org-level permissions.
    """
    actor = Identity.for_agent("test_agent", uuid.uuid4())

    captured_filters: list[SearchFilters] = []

    async def fake_search(session, query, filters, top_k, *args, **kwargs):
        captured_filters.append(filters)
        return [_chunk({}) for _ in range(5)]

    monkeypatch.setattr(evidence_module.retrieval_service, "search", fake_search)

    await gather_evidence(session=None, query="checkout failing", actor=actor, retry_count={})

    assert captured_filters, "retrieval.service.search was never called"
    for filters in captured_filters:
        assert filters.project_ids is None


@pytest.mark.asyncio
async def test_gather_live_evidence_logs_and_skips_source_that_raises(monkeypatch) -> None:
    """One connector's live lookup failing (bad token, rate limit, timeout)
    must not affect any other connector's -- same non-fatal discipline as
    every other source in this module.
    """
    import app.agents.retry as retry_module

    async def instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(retry_module.asyncio, "sleep", instant_sleep)

    github_config = _connector_config("github")

    async def fake_list_connectors(session, actor, organization_id):
        return [github_config]

    monkeypatch.setattr(evidence_module.tenancy_service, "list_connectors", fake_list_connectors)

    class _FailingLiveSource:
        async def fetch_live_evidence(self, **kwargs):
            raise RuntimeError("github search failed")

    monkeypatch.setattr(evidence_module, "_LIVE_SOURCES", {"github": _FailingLiveSource()})

    actor = Identity.for_agent("test_agent", uuid.uuid4())
    result = await _gather_live_evidence(session=None, query="q", actor=actor, retry_count={})

    assert result == []
