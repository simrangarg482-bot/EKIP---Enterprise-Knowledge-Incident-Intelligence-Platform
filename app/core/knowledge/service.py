"""Public interface for core/knowledge -- the proposed-document review
lifecycle (API_DESIGN.md section 1 "Knowledge review queue", section 3's
`propose_runbook_update` tool and `document://` resource).

This is the core-owned "documents" read/write surface that
`app.mcp.tools`'s and `app.mcp.resources`'s own module docstrings flagged as
missing when Milestone 8 was first wired up -- closing it unblocks
`propose_runbook_update`, the `document://` resource, and the REST
knowledge-review endpoints all at once, and is also the exact surface the
future Knowledge Gap Agent will need (its entire output is this same kind
of document proposal, AGENT_WORKFLOWS.md section 2.6).

Tenant isolation follows the same `_ensure_same_organization` /
`_get_owned_*` convention already used in `core.incidents.service` /
`core.tenancy.service` / `core.audit.service`.

Permission design: `propose_document` is deliberately NOT gated by a
special permission code -- recording a low-risk, fully reversible proposal
awaiting human review is the same category as
`core.incidents.service.record_investigation_result` (a system/agent-
recorded fact, not itself a privileged action). `publish_document`,
`reject_document`, and `list_proposed_documents` ARE gated by
`knowledge:review`, since those are the actual human-approval-gate actions
`ARCHITECTURE.md` section 5 requires ("nothing an agent proposes reaches
'knowledge' without this").
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.service import record_audit_event
from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.knowledge import repository
from app.core.knowledge.schemas import Document, DocumentProposalCreate, DocumentUpdate
from app.core.tenancy import service as tenancy_service
from app.core.users.service import require_permission, require_project_permission
from app.database.models.ingestion_models import Document as DocumentRow
from app.retrieval import service as retrieval_service
from app.retrieval.schemas import UpsertChunk
from app.shared.config.logging import get_logger
from app.shared.schemas import Identity

logger = get_logger(__name__)

_REVIEW_PERMISSION = "knowledge:review"
_CONTENT_METADATA_KEY = "content"
_SOURCE_INCIDENT_METADATA_KEY = "source_incident_id"


def _ensure_same_organization(actor: Identity, organization_id: uuid.UUID) -> None:
    """Tenant-isolation guard -- identical in spirit to the copies in
    `core.tenancy.service`/`core.incidents.service`/`core.audit.service`
    (now a fourth occurrence; still not extracted into `shared/`, per those
    modules' own notes on when that becomes worth doing).
    """
    if actor.organization_id != organization_id:
        logger.warning(
            "knowledge_cross_organization_denied",
            actor=actor.audit_tag,
            actor_organization_id=str(actor.organization_id),
            requested_organization_id=str(organization_id),
        )
        raise PermissionDeniedError(
            "Cannot access another organization's data.",
            error_code="knowledge.cross_organization_denied",
            detail={"organization_id": str(organization_id)},
        )


async def _get_owned_document(
    session: AsyncSession, organization_id: uuid.UUID, document_id: uuid.UUID
) -> DocumentRow:
    """Fetch a document ORM row, raising `NotFoundError` unless it exists,
    belongs to `organization_id`, and is not soft-deleted -- `NotFoundError`'s
    own docstring already scopes it to "does not exist (or is soft-deleted /
    hidden)", so a rejected (soft-deleted) proposal reads as not-found here,
    not as a still-visible-but-rejected row.
    """
    row = await repository.get_document_by_id(session, document_id)
    if row is None or row.organization_id != organization_id or row.deleted_at is not None:
        raise NotFoundError(
            "Document not found.",
            error_code="document.not_found",
            detail={"document_id": str(document_id)},
        )
    return row


async def _to_schema(session: AsyncSession, row: DocumentRow) -> Document:
    content = await repository.get_metadata_value(session, row.id, _CONTENT_METADATA_KEY)
    source_incident_id_raw = await repository.get_metadata_value(
        session, row.id, _SOURCE_INCIDENT_METADATA_KEY
    )
    return Document(
        id=row.id,
        organization_id=row.organization_id,
        project_id=row.project_id,
        title=row.title,
        status=row.status,
        version=row.version,
        content=content,
        source=row.source,
        source_url=row.source_url,
        source_incident_id=uuid.UUID(source_incident_id_raw) if source_incident_id_raw else None,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def propose_document(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    data: DocumentProposalCreate,
) -> Document:
    """Create a new proposed document (`status="proposed"`), awaiting human
    review via `publish_document`/`reject_document`.
    """
    _ensure_same_organization(actor, organization_id)

    project_id = data.project_id
    if project_id is None:
        default_project = await tenancy_service.get_default_project(session, actor, organization_id)
        project_id = default_project.id

    content_hash = hashlib.sha256(data.content.encode("utf-8")).hexdigest()
    row = await repository.insert_document(
        session,
        organization_id=organization_id,
        project_id=project_id,
        external_id=str(uuid.uuid4()),
        content_hash=content_hash,
        title=data.title,
    )
    await repository.insert_metadata(
        session, document_id=row.id, key=_CONTENT_METADATA_KEY, value=data.content
    )
    if data.source_incident_id is not None:
        await repository.insert_metadata(
            session,
            document_id=row.id,
            key=_SOURCE_INCIDENT_METADATA_KEY,
            value=str(data.source_incident_id),
        )

    await record_audit_event(
        session,
        actor,
        action="document.propose",
        resource_type="document",
        resource_id=row.id,
        metadata={"title": data.title},
    )
    logger.info("document_proposed", document_id=str(row.id), organization_id=str(organization_id))
    return await _to_schema(session, row)


async def get_document(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    """Fetch one document. Published documents are readable by anyone in
    the organization; a still-proposed one requires `knowledge:review`
    (API_DESIGN.md section 3's `document://` resource docstring: "published
    documents only, unless the requesting identity has knowledge:review").

    The permission check is project-scoped (`row.project_id`): a caller
    holding `knowledge:review` only on this document's project, but not at
    the organization level, may still view it. `Identity.has_permission`'s
    own semantics mean this changes nothing for callers with no per-project
    override -- they still fall back to the org-level check exactly as
    before.
    """
    _ensure_same_organization(actor, organization_id)
    row = await _get_owned_document(session, organization_id, document_id)
    if row.status != "published" and not actor.has_permission(
        _REVIEW_PERMISSION, project_id=row.project_id
    ):
        raise PermissionDeniedError(
            "This document has not been published yet.",
            error_code="document.not_published",
            detail={"document_id": str(document_id)},
        )
    return await _to_schema(session, row)


async def list_proposed_documents(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID
) -> list[Document]:
    """List every proposed document awaiting review (API_DESIGN.md:
    `GET /knowledge/proposed`).
    """
    _ensure_same_organization(actor, organization_id)
    require_permission(actor, _REVIEW_PERMISSION)

    rows = await repository.list_proposed_documents(session, organization_id)
    return [await _to_schema(session, row) for row in rows]


async def list_published_documents(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    *,
    source: str | None = None,
    updated_since: datetime | None = None,
) -> list[Document]:
    """List published documents for browsing (`GET /knowledge`) -- "browse
    ingested GitHub/Slack data". No `knowledge:review` gate, unlike
    `list_proposed_documents`: matches `get_document`'s existing rule that a
    published document is readable by anyone in the organization, so this is
    a plain org-scoped read, not a review-queue action.
    """
    _ensure_same_organization(actor, organization_id)

    rows = await repository.list_published_documents(
        session, organization_id, source=source, updated_since=updated_since
    )
    return [await _to_schema(session, row) for row in rows]


async def publish_document(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    """Publish a proposed document -- the human-review gate (API_DESIGN.md:
    `POST /knowledge/{document_id}/publish`) -- and embed it into the
    retrieval index in the same transaction ("triggering embedding into the
    retrieval index", per that endpoint's own documented behavior).

    Embeds the whole `content` as a single `UpsertChunk` (`collection=
    "documentation"`): unlike ingestion's connector pipeline, a manually
    proposed runbook has no raw-content-cleaning/multi-chunk pipeline to run
    through (`app.ingestion.processors` is off-limits to `core/` regardless
    -- see `repository.py`'s module docstring), and a review-quality runbook
    is typically short enough that one chunk is an honest, reasonable
    simplification rather than a silently cut corner. A future improvement
    could reuse a shared chunking utility if one is ever extracted out of
    `app.ingestion.processors` into somewhere both modules may import.

    The `knowledge:review` check is project-scoped to `row.project_id`
    (`require_project_permission`) -- fetching the row before checking
    (rather than the reverse, as this function did before project-scoped
    enforcement existed) is required to know which project to check against;
    an unauthorized caller now learns a nonexistent/foreign document 404s the
    same as always (`_get_owned_document` already tenant-scopes it), so this
    reordering does not newly leak document existence across organizations,
    only slightly changes 403-vs-404 precedence for a caller in the *same*
    organization who lacks the permission -- an accepted, minor behavior
    change in exchange for real per-project enforcement.
    """
    _ensure_same_organization(actor, organization_id)
    row = await _get_owned_document(session, organization_id, document_id)
    require_project_permission(actor, row.project_id, _REVIEW_PERMISSION)

    if row.status != "proposed":
        raise ConflictError(
            "Only a pending proposal can be published.",
            error_code="document.not_proposed",
            detail={"status": row.status},
        )

    content = await repository.get_metadata_value(session, row.id, _CONTENT_METADATA_KEY)
    updated = await repository.update_document_status(session, document_id, status="published")
    if updated is None:
        raise RuntimeError("Document disappeared mid-update.")  # unreachable: fetched above

    if content:
        chunk = UpsertChunk(
            document_id=row.id,
            organization_id=organization_id,
            project_id=row.project_id,
            collection="documentation",
            chunk_index=0,
            content=content,
            source_offset_start=0,
            source_offset_end=len(content),
        )
        await retrieval_service.upsert(session, [chunk])

    await record_audit_event(
        session,
        actor,
        action="document.publish",
        resource_type="document",
        resource_id=document_id,
        metadata={},
    )
    logger.info("document_published", document_id=str(document_id))
    return await _to_schema(session, updated)


async def update_document(
    session: AsyncSession,
    actor: Identity,
    organization_id: uuid.UUID,
    document_id: uuid.UUID,
    data: DocumentUpdate,
) -> Document:
    """Edit a still-proposed document's `title`/`content` during human
    review (`PATCH /knowledge/{document_id}`) -- a new capability alongside
    publish/reject, gated by the same `knowledge:review` permission (same
    tier as approving/rejecting a proposal outright, per this module's own
    permission-design docstring).

    Only a `"proposed"` document may be edited here -- once published,
    content is considered final and already embedded into the retrieval
    index (`publish_document`); editing it through this function afterward
    would silently desynchronize the two without re-triggering that
    embedding step. A published document's content is not editable through
    this endpoint at all (raises ConflictError), matching `publish_document`/
    `reject_document`'s identical `status != "proposed"` guard.

    `exclude_unset`: omitting a field leaves it untouched. Editing `content`
    updates the same `document_metadata` `"content"` key `propose_document`
    wrote (via `repository.upsert_metadata`, which replaces the existing row
    in place rather than duplicating it -- see that function's own
    docstring). Editing `title` (or `content`, when `title` is untouched)
    bumps `Document.version`, matching the version-bump-on-content-change
    convention `Document`'s own docstring already establishes for ingestion
    re-syncs.
    """
    _ensure_same_organization(actor, organization_id)
    row = await _get_owned_document(session, organization_id, document_id)
    require_project_permission(actor, row.project_id, _REVIEW_PERMISSION)

    if row.status != "proposed":
        raise ConflictError(
            "Only a pending proposal can be edited.",
            error_code="document.not_proposed",
            detail={"status": row.status},
        )

    fields = data.model_dump(exclude_unset=True)
    if not fields:
        return await _to_schema(session, row)

    if "title" in fields:
        updated = await repository.update_document_title(session, document_id, title=fields["title"])
    else:
        updated = await repository.bump_document_version(session, document_id)
    if updated is None:
        raise RuntimeError("Document disappeared mid-update.")  # unreachable: fetched above

    if "content" in fields:
        await repository.upsert_metadata(
            session, document_id=document_id, key=_CONTENT_METADATA_KEY, value=fields["content"]
        )

    await record_audit_event(
        session,
        actor,
        action="document.update",
        resource_type="document",
        resource_id=document_id,
        metadata={"organization_id": str(organization_id), "changed_fields": list(fields.keys())},
    )
    logger.info("document_updated", document_id=str(document_id))
    return await _to_schema(session, updated)


async def reject_document(
    session: AsyncSession, actor: Identity, organization_id: uuid.UUID, document_id: uuid.UUID
) -> Document:
    """Discard a proposal (API_DESIGN.md: `POST /knowledge/{document_id}/reject`).

    `shared.schemas.DocumentStatus` has only `"proposed"`/`"published"` --
    no third "rejected" value -- so rejection is represented as a soft
    delete (`deleted_at` set, `status` left at `"proposed"`) rather than
    silently inventing a status value this project's own vocabulary doesn't
    have. `_get_owned_document` already treats a soft-deleted row as
    not-found, so a rejected proposal cannot resurface through
    `get_document`/`list_proposed_documents` afterward.

    Project-scoped `knowledge:review` check, same reasoning and same
    fetch-before-check reordering as `publish_document`.
    """
    _ensure_same_organization(actor, organization_id)
    row = await _get_owned_document(session, organization_id, document_id)
    require_project_permission(actor, row.project_id, _REVIEW_PERMISSION)

    if row.status != "proposed":
        raise ConflictError(
            "Only a pending proposal can be rejected.",
            error_code="document.not_proposed",
            detail={"status": row.status},
        )

    updated = await repository.soft_delete_document(
        session, document_id, deleted_at=datetime.now(timezone.utc)
    )
    if updated is None:
        raise RuntimeError("Document disappeared mid-update.")  # unreachable: fetched above

    await record_audit_event(
        session,
        actor,
        action="document.reject",
        resource_type="document",
        resource_id=document_id,
        metadata={},
    )
    logger.info("document_rejected", document_id=str(document_id))
    return await _to_schema(session, updated)
