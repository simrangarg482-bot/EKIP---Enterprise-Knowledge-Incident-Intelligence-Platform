"""Knowledge-review-queue router (API_DESIGN.md section 1, "Knowledge
review queue" and "Knowledge gaps").

Owned by: app/api. `/knowledge/proposed`, `/publish`, `/reject` are a thin
pass-through to `app.core.knowledge.service`. `/knowledge/gaps` (Milestone
9) is a thin pass-through to `app.agents.service.list_gap_reports` --
previously unwired because the Knowledge Gap Agent didn't exist yet; now
that it does, this closes the last of API_DESIGN.md section 1's documented
REST endpoints for this resource group.

`propose_runbook_update` (creating a proposal) is intentionally not exposed
here as its own REST endpoint: API_DESIGN.md's REST table only lists list/
publish/reject/gaps for this resource group -- proposing one is documented
only as an MCP tool contract (section 3), not a REST-facing action in the
current spec. `GET /{document_id}` and `PATCH /{document_id}` (human-review
improvements added alongside project-scoped RBAC/logout-everywhere) ARE new
REST surface, but both are still read/edit operations on an existing
proposal, not a second way to create one -- that boundary is unchanged.

Route-ordering note: `GET /{document_id}` is declared after `/proposed` and
`/gaps` so FastAPI's literal-prefix routes are never shadowed by the
`{document_id}` path parameter (a literal path always needs to be registered
before a variable one that could otherwise swallow it).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter

from app.agents import service as agents_service
from app.api.deps import CurrentIdentity, DbSession
from app.core.knowledge import service as knowledge_service
from app.core.knowledge.schemas import Document, DocumentUpdate
from app.shared.schemas import GapReport

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=list[Document])
async def list_published_documents(
    actor: CurrentIdentity,
    session: DbSession,
    source: str | None = None,
    updated_since: datetime | None = None,
) -> list[Document]:
    """Browse published, already-ingested knowledge (`source="github"`/
    `"slack"`/`"manual"`/...) -- see `knowledge_service.list_published_
    documents`'s docstring for why this has no `knowledge:review` gate,
    unlike `/proposed` below.
    """
    return await knowledge_service.list_published_documents(
        session, actor, actor.organization_id, source=source, updated_since=updated_since
    )


@router.get("/proposed", response_model=list[Document])
async def list_proposed_documents(actor: CurrentIdentity, session: DbSession) -> list[Document]:
    return await knowledge_service.list_proposed_documents(session, actor, actor.organization_id)


@router.get("/gaps", response_model=list[GapReport])
async def list_gap_reports(actor: CurrentIdentity, session: DbSession) -> list[GapReport]:
    return await agents_service.list_gap_reports(session, actor)


@router.get("/{document_id}", response_model=Document)
async def get_document(
    document_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> Document:
    return await knowledge_service.get_document(session, actor, actor.organization_id, document_id)


@router.patch("/{document_id}", response_model=Document)
async def update_document(
    document_id: uuid.UUID, data: DocumentUpdate, actor: CurrentIdentity, session: DbSession
) -> Document:
    return await knowledge_service.update_document(
        session, actor, actor.organization_id, document_id, data
    )


@router.post("/{document_id}/publish", response_model=Document)
async def publish_document(
    document_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> Document:
    return await knowledge_service.publish_document(
        session, actor, actor.organization_id, document_id
    )


@router.post("/{document_id}/reject", response_model=Document)
async def reject_document(
    document_id: uuid.UUID, actor: CurrentIdentity, session: DbSession
) -> Document:
    return await knowledge_service.reject_document(
        session, actor, actor.organization_id, document_id
    )
