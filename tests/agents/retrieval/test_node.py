"""Regression tests for `app.agents.retrieval.node`'s `SearchFilters`
construction.

Confirmed bug (2026-08 audit, "C1"): the Retrieval Agent node built
`SearchFilters` with `project_ids` left at its default (`None` = "every
project in the organization"), regardless of whether the calling actor
actually held any project-scoped membership. A user who belongs only to
Project A (not Project B) in the same organization could therefore still
retrieve Project B's chunks via `ask_question`, whenever the target chunk's
`acl_permission_code` was `None` or covered by an org-level permission grant
the user held independently of project membership.

The fix (`Identity.resolve_search_scope`, `app/shared/schemas/identity.py`)
is exercised here by asserting on the actual `SearchFilters` object the node
passes to `retrieval.service.search` -- not by asserting on
`Identity.resolve_search_scope` in isolation (see `tests/shared/
test_identity.py` for that), since the bug was specifically that this call
site never consulted it.
"""

from __future__ import annotations

import uuid

import pytest

from app.agents.graph import GraphState
from app.agents.retrieval import node as node_module
from app.retrieval.schemas import ScoredChunk, SearchFilters
from app.shared.schemas import ActorKind, Identity


def _actor(
    organization_id: uuid.UUID,
    *,
    permissions: frozenset[str] = frozenset(),
    project_permissions: dict[uuid.UUID, frozenset[str]] | None = None,
) -> Identity:
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        permissions=permissions,
        project_permissions=project_permissions or {},
    )


def _chunk() -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection="documentation",
        content="content",
        score=0.9,
        source_offset_start=0,
        source_offset_end=7,
    )


async def _run_node_and_capture_filters(monkeypatch, actor: Identity) -> SearchFilters:
    """Wires just enough of the node's dependencies to reach the
    `retrieval.service.search` call and capture the `SearchFilters` it was
    given, without needing a real LLM, embedding model, or database.
    """
    captured: dict[str, object] = {}

    async def fake_rewrite_query(session, *, query, incident_id, actor, llm, retry_count):
        return query

    async def fake_search(session, query, filters, top_k):
        captured["filters"] = filters
        return [_chunk()]

    async def fake_rerank(query, candidates, *, top_k):
        return candidates

    def fake_assemble_context(chunks):
        return chunks

    monkeypatch.setattr(node_module, "rewrite_query", fake_rewrite_query)
    monkeypatch.setattr(node_module.retrieval_service, "search", fake_search)
    monkeypatch.setattr(node_module, "rerank", fake_rerank)
    monkeypatch.setattr(node_module, "assemble_context", fake_assemble_context)

    node = node_module.make_retrieval_agent_node(session=None, llm=None)
    state = GraphState(query="how do I deploy the checkout service?", actor=actor)
    await node(state)

    assert "filters" in captured, "retrieval.service.search was never called"
    return captured["filters"]  # type: ignore[return-value]


@pytest.mark.asyncio
async def test_actor_with_no_project_memberships_searches_unrestricted_by_project(
    monkeypatch,
) -> None:
    """Preserves existing behavior for the common case: a caller with only
    org-level permissions (no `project_memberships` row at all) must still
    be able to search every project they can already see via their
    org-level grant -- this fix must not regress that.
    """
    organization_id = uuid.uuid4()
    actor = _actor(organization_id, permissions=frozenset({"observability:read"}))

    filters = await _run_node_and_capture_filters(monkeypatch, actor)

    assert filters.organization_id == organization_id
    assert filters.project_ids is None
    assert filters.permission_codes == frozenset({"observability:read"})


@pytest.mark.asyncio
async def test_actor_with_project_membership_is_restricted_to_that_project(
    monkeypatch,
) -> None:
    """The confirmed-leak scenario: an actor who belongs to Project A only
    must not be able to search Project B's chunks. `project_ids` must be
    restricted to exactly the projects the actor holds a membership row
    for.
    """
    organization_id = uuid.uuid4()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()  # the actor has NO membership row for this project
    actor = _actor(
        organization_id,
        permissions=frozenset({"observability:read"}),
        project_permissions={project_a: frozenset({"incident:write"})},
    )

    filters = await _run_node_and_capture_filters(monkeypatch, actor)

    assert filters.project_ids == [project_a]
    assert project_b not in (filters.project_ids or [])
    # Project-scoped grants are merged in alongside org-level permissions,
    # not substituted for them.
    assert filters.permission_codes == frozenset({"observability:read", "incident:write"})


@pytest.mark.asyncio
async def test_actor_with_multiple_project_memberships_sees_only_those_projects(
    monkeypatch,
) -> None:
    organization_id = uuid.uuid4()
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    project_c = uuid.uuid4()  # no membership row -- must never appear
    actor = _actor(
        organization_id,
        project_permissions={
            project_a: frozenset({"incident:write"}),
            project_b: frozenset({"postmortem:write"}),
        },
    )

    filters = await _run_node_and_capture_filters(monkeypatch, actor)

    assert set(filters.project_ids or []) == {project_a, project_b}
    assert project_c not in (filters.project_ids or [])
