"""Pydantic contracts for retrieval/.

Owned by: retrieval/. Per PROJECT_PLAN.md section 9.9, retrieval "must never
... know anything about incidents, postmortems, agents, or
organizations-as-a-concept beyond 'a filter value' -- it only knows
documents, chunks, and queries." Concretely: nothing in this module imports
`app.shared.schemas.Identity` or any RBAC concept. `SearchFilters` is a
plain value object -- organization_id, project_ids, permission codes --
that whatever calls `retrieval.search()` (the not-yet-built Retrieval Agent,
Milestone 6) is responsible for resolving from an `Identity` *before*
calling in. Resolving "which projects can this caller see" from
`Identity.project_permissions` is deliberately NOT retrieval's job.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# One collection per content category (PROJECT_PLAN.md section 8.2), mapping
# 1:1 onto `app.ingestion.processors.chunking.ContentType` ("document" ->
# "documentation", "chat" -> "conversations", "code" -> "code") -- see
# `app.database.models.retrieval_models`'s module docstring for why no
# `"incidents"` collection exists yet (section 8.2 names one, but nothing
# produces embeddable chunks for it today).
CollectionName = Literal["documentation", "code", "conversations"]


class SearchFilters(BaseModel):
    """The hard constraints applied *on the search query itself*
    (PROJECT_PLAN.md section 5.4-5.5), never as a post-filter on results.

    `project_ids=None` means "no project-level restriction" (the caller
    resolved that they may search every project in the organization --
    the common case for a caller with only org-level permissions, no
    per-project overrides). A non-`None` list restricts to exactly those
    project ids. `permission_codes` is checked against each chunk's
    (denormalized) `acl_permission_code`: a chunk with `acl_permission_code
    = None` is unrestricted; one with a code set requires that code to be
    present in this set (ENGINEERING_DECISIONS.md #007).
    """

    model_config = ConfigDict(frozen=True)

    organization_id: uuid.UUID
    project_ids: list[uuid.UUID] | None = None
    permission_codes: frozenset[str] = Field(default_factory=frozenset)
    # Restricts results to one GitHub repo (`"owner/name"`, matching
    # `document_metadata`'s `repo` entry / `CodeChunk.repo_full_name`).
    # Only meaningful for the `"code"` collection -- `PgVectorStore` raises
    # if this is set while searching any other collection, since no other
    # collection's table carries a `repo_full_name` column to filter on.
    repository: str | None = None


class UpsertChunk(BaseModel):
    """One chunk to embed and store, as passed to `retrieval.upsert()`.

    Carries `organization_id`/`project_id`/`acl_permission_code` directly
    (not just `document_id`, requiring a join to look them up) so
    `VectorStore` implementations can apply `SearchFilters` as a hard
    constraint on the stored row itself, matching the same
    "denormalize the tenant columns onto every row an RLS policy or query
    filter needs to check directly" convention already used for
    `IncidentTimeline`/`Postmortem`/`IngestionJob`.

    Does NOT carry an embedding vector: `retrieval.upsert()` computes it
    internally (task #14's embedding module) -- callers hand over content to
    be embedded, not a precomputed vector, matching PROJECT_PLAN.md section
    9.9's `upsert(chunks)` signature (no separate embed step exposed).
    """

    model_config = ConfigDict(frozen=True)

    document_id: uuid.UUID
    organization_id: uuid.UUID
    project_id: uuid.UUID
    collection: CollectionName
    chunk_index: int
    content: str
    source_offset_start: int
    source_offset_end: int
    acl_permission_code: str | None = None
    # Denormalized `"owner/name"` for GitHub-sourced chunks (from the
    # connector's `document_metadata` `repo` entry), so `SearchFilters.
    # repository` can filter on the stored row directly instead of joining
    # back to `document_metadata`. `None` for every non-GitHub chunk.
    repo_full_name: str | None = None


class ScoredChunk(BaseModel):
    """One retrieved chunk plus its relevance score, as returned by
    `retrieval.search()` (PROJECT_PLAN.md section 6.1's
    `retrieved_chunks: list[ScoredChunk]`).

    `title`/`source_url` come from a join back to `documents` at query time
    (section 9.9: "Dependencies: database (metadata joins)") -- retrieval
    reads that ingestion-owned table directly for this purpose, the same
    kind of direct-read exception already established for ingestion reading
    `connector_configs` (see `app.ingestion.repository`'s module docstring).

    `metadata` is the document's `document_metadata` EAV rows folded into a
    dict, populated *only* when the caller passed `include_metadata=True` to
    `retrieval.search()`/`PgVectorStore.search`/`.lexical_search` -- empty
    otherwise. Opt-in rather than always-joined: the Answer Agent's path
    calls `search()` up to six times per question and has never needed this;
    only the Investigation Agent's evidence-gathering step (which needs to
    tell a GitHub file apart from a commit/PR/issue chunk, per that agent's
    own docstring) does. Defaulting this join off keeps the common,
    higher-volume path's query cost unchanged.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    collection: CollectionName
    content: str
    score: float
    source_offset_start: int
    source_offset_end: int
    title: str | None = None
    source_url: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
