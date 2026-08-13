"""Tests for `app.core.knowledge.service` -- the proposed-document review
lifecycle that unblocked `propose_runbook_update`, the `document://`
resource, and the REST knowledge-review endpoints.

`repository.py`'s functions are monkeypatched with in-memory fakes (same
style as `tests/agents/investigation/test_evidence.py`'s
`tenancy_service.list_connectors` patches) -- no real database, no ORM
session behavior exercised here, only `service.py`'s own tenant-isolation,
permission, and status-transition logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.core.knowledge import service as knowledge_service
from app.core.knowledge.schemas import DocumentProposalCreate, DocumentUpdate
from app.retrieval.schemas import UpsertChunk
from app.shared.schemas import ActorKind, Identity


class _FakeRow:
    def __init__(self, **kwargs: object) -> None:
        now = datetime.now(timezone.utc)
        self.id: uuid.UUID = kwargs.get("id", uuid.uuid4())  # type: ignore[assignment]
        self.organization_id: uuid.UUID = kwargs["organization_id"]  # type: ignore[assignment]
        self.project_id: uuid.UUID = kwargs.get("project_id", uuid.uuid4())  # type: ignore[assignment]
        self.title: str | None = kwargs.get("title", "A runbook")  # type: ignore[assignment]
        self.status: str = kwargs.get("status", "proposed")  # type: ignore[assignment]
        self.version: int = kwargs.get("version", 1)  # type: ignore[assignment]
        self.source: str = kwargs.get("source", "manual")  # type: ignore[assignment]
        self.source_url: str | None = kwargs.get("source_url")
        self.deleted_at = kwargs.get("deleted_at")
        self.created_at = kwargs.get("created_at", now)
        self.updated_at = kwargs.get("updated_at", now)


def _reviewer(organization_id: uuid.UUID) -> Identity:
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        permissions=frozenset({"knowledge:review"}),
    )


def _non_reviewer(organization_id: uuid.UUID) -> Identity:
    return Identity.for_agent("test_agent", organization_id)


@pytest.mark.asyncio
async def test_propose_document_stores_content_as_metadata(monkeypatch) -> None:
    actor = _non_reviewer(uuid.uuid4())
    row = _FakeRow(organization_id=actor.organization_id)
    inserted_metadata: list[dict[str, object]] = []

    async def fake_insert_document(session, **kwargs):
        row.title = kwargs.get("title", row.title)
        return row

    async def fake_insert_metadata(session, *, document_id, key, value):
        inserted_metadata.append({"document_id": document_id, "key": key, "value": value})

    async def fake_get_metadata_value(session, document_id, key):
        for entry in inserted_metadata:
            if entry["document_id"] == document_id and entry["key"] == key:
                return entry["value"]
        return None

    async def fake_record_audit_event(session, actor, **kwargs):
        return None

    monkeypatch.setattr(knowledge_service.repository, "insert_document", fake_insert_document)
    monkeypatch.setattr(knowledge_service.repository, "insert_metadata", fake_insert_metadata)
    monkeypatch.setattr(knowledge_service.repository, "get_metadata_value", fake_get_metadata_value)
    monkeypatch.setattr(knowledge_service, "record_audit_event", fake_record_audit_event)

    data = DocumentProposalCreate(
        title="Checkout 500 runbook",
        content="Restart the checkout service and clear the queue.",
        project_id=row.project_id,
    )
    document = await knowledge_service.propose_document(None, actor, actor.organization_id, data)

    assert document.status == "proposed"
    assert document.title == "Checkout 500 runbook"
    assert document.content == "Restart the checkout service and clear the queue."
    assert any(m["key"] == "content" for m in inserted_metadata)


@pytest.mark.asyncio
async def test_propose_document_denies_cross_organization() -> None:
    actor = _non_reviewer(uuid.uuid4())
    data = DocumentProposalCreate(title="t", content="c")

    with pytest.raises(PermissionDeniedError):
        await knowledge_service.propose_document(None, actor, uuid.uuid4(), data)


@pytest.mark.asyncio
async def test_get_document_denies_unpublished_without_review_permission(monkeypatch) -> None:
    actor = _non_reviewer(uuid.uuid4())
    row = _FakeRow(organization_id=actor.organization_id, status="proposed")

    async def fake_get_document_by_id(session, document_id):
        return row

    async def fake_get_metadata_value(session, document_id, key):
        return None

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)
    monkeypatch.setattr(knowledge_service.repository, "get_metadata_value", fake_get_metadata_value)

    with pytest.raises(PermissionDeniedError):
        await knowledge_service.get_document(None, actor, actor.organization_id, row.id)


@pytest.mark.asyncio
async def test_get_document_allows_unpublished_with_review_permission(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)
    row = _FakeRow(organization_id=organization_id, status="proposed")

    async def fake_get_document_by_id(session, document_id):
        return row

    async def fake_get_metadata_value(session, document_id, key):
        return None

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)
    monkeypatch.setattr(knowledge_service.repository, "get_metadata_value", fake_get_metadata_value)

    document = await knowledge_service.get_document(None, actor, organization_id, row.id)
    assert document.status == "proposed"


@pytest.mark.asyncio
async def test_get_document_treats_soft_deleted_row_as_not_found(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)
    row = _FakeRow(
        organization_id=organization_id, status="proposed", deleted_at=datetime.now(timezone.utc)
    )

    async def fake_get_document_by_id(session, document_id):
        return row

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)

    with pytest.raises(NotFoundError):
        await knowledge_service.get_document(None, actor, organization_id, row.id)


@pytest.mark.asyncio
async def test_list_proposed_documents_requires_review_permission() -> None:
    actor = _non_reviewer(uuid.uuid4())
    with pytest.raises(PermissionDeniedError):
        await knowledge_service.list_proposed_documents(None, actor, actor.organization_id)


@pytest.mark.asyncio
async def test_publish_document_transitions_status_and_upserts_chunk(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)
    row = _FakeRow(organization_id=organization_id, status="proposed")
    content = "Restart the checkout service."

    async def fake_get_document_by_id(session, document_id):
        return row

    async def fake_get_metadata_value(session, document_id, key):
        return content if key == "content" else None

    published_row = _FakeRow(
        id=row.id,
        organization_id=organization_id,
        project_id=row.project_id,
        status="published",
    )

    async def fake_update_document_status(session, document_id, *, status):
        published_row.status = status
        return published_row

    upserted_chunks: list[UpsertChunk] = []

    async def fake_upsert(session, chunks):
        upserted_chunks.extend(chunks)

    async def fake_record_audit_event(session, actor, **kwargs):
        return None

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)
    monkeypatch.setattr(knowledge_service.repository, "get_metadata_value", fake_get_metadata_value)
    monkeypatch.setattr(
        knowledge_service.repository, "update_document_status", fake_update_document_status
    )
    monkeypatch.setattr(knowledge_service.retrieval_service, "upsert", fake_upsert)
    monkeypatch.setattr(knowledge_service, "record_audit_event", fake_record_audit_event)

    document = await knowledge_service.publish_document(None, actor, organization_id, row.id)

    assert document.status == "published"
    assert len(upserted_chunks) == 1
    assert upserted_chunks[0].content == content
    assert upserted_chunks[0].collection == "documentation"


@pytest.mark.asyncio
async def test_publish_document_rejects_already_published(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)
    row = _FakeRow(organization_id=organization_id, status="published")

    async def fake_get_document_by_id(session, document_id):
        return row

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)

    with pytest.raises(ConflictError):
        await knowledge_service.publish_document(None, actor, organization_id, row.id)


@pytest.mark.asyncio
async def test_reject_document_soft_deletes(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)
    row = _FakeRow(organization_id=organization_id, status="proposed")
    captured: dict[str, object] = {}

    async def fake_get_document_by_id(session, document_id):
        return row

    async def fake_get_metadata_value(session, document_id, key):
        return None

    async def fake_soft_delete_document(session, document_id, *, deleted_at):
        captured["deleted_at"] = deleted_at
        row.deleted_at = deleted_at
        return row

    async def fake_record_audit_event(session, actor, **kwargs):
        return None

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)
    monkeypatch.setattr(knowledge_service.repository, "get_metadata_value", fake_get_metadata_value)
    monkeypatch.setattr(
        knowledge_service.repository, "soft_delete_document", fake_soft_delete_document
    )
    monkeypatch.setattr(knowledge_service, "record_audit_event", fake_record_audit_event)

    document = await knowledge_service.reject_document(None, actor, organization_id, row.id)

    assert captured["deleted_at"] is not None
    # `status` is left at "proposed" -- rejection is a soft delete, not a
    # third status value (see service.py's docstring).
    assert document.status == "proposed"


# --- update_document (human-review editing, PATCH /knowledge/{document_id}) --


@pytest.mark.asyncio
async def test_update_document_edits_title_and_content_bumps_version(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)
    row = _FakeRow(organization_id=organization_id, status="proposed", title="Old title", version=1)
    inserted_metadata: list[dict[str, object]] = []

    async def fake_get_document_by_id(session, document_id):
        return row

    async def fake_get_metadata_value(session, document_id, key):
        for entry in reversed(inserted_metadata):
            if entry["document_id"] == document_id and entry["key"] == key:
                return entry["value"]
        return None

    updated_row = _FakeRow(
        id=row.id,
        organization_id=organization_id,
        project_id=row.project_id,
        status="proposed",
        title="New title",
        version=2,
    )

    async def fake_update_document_title(session, document_id, *, title):
        updated_row.title = title
        return updated_row

    async def fake_upsert_metadata(session, *, document_id, key, value):
        inserted_metadata.append({"document_id": document_id, "key": key, "value": value})

    async def fake_record_audit_event(session, actor, **kwargs):
        return None

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)
    monkeypatch.setattr(knowledge_service.repository, "get_metadata_value", fake_get_metadata_value)
    monkeypatch.setattr(
        knowledge_service.repository, "update_document_title", fake_update_document_title
    )
    monkeypatch.setattr(knowledge_service.repository, "upsert_metadata", fake_upsert_metadata)
    monkeypatch.setattr(knowledge_service, "record_audit_event", fake_record_audit_event)

    document = await knowledge_service.update_document(
        None,
        actor,
        organization_id,
        row.id,
        DocumentUpdate(title="New title", content="New content"),
    )

    assert document.title == "New title"
    assert document.version == 2
    assert document.content == "New content"
    assert any(m["key"] == "content" and m["value"] == "New content" for m in inserted_metadata)


@pytest.mark.asyncio
async def test_update_document_with_no_fields_is_a_no_op(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)
    row = _FakeRow(organization_id=organization_id, status="proposed")

    async def fake_get_document_by_id(session, document_id):
        return row

    async def fake_get_metadata_value(session, document_id, key):
        return None

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)
    monkeypatch.setattr(knowledge_service.repository, "get_metadata_value", fake_get_metadata_value)

    document = await knowledge_service.update_document(
        None, actor, organization_id, row.id, DocumentUpdate()
    )

    assert document.version == row.version


@pytest.mark.asyncio
async def test_update_document_rejects_already_published(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _reviewer(organization_id)
    row = _FakeRow(organization_id=organization_id, status="published")

    async def fake_get_document_by_id(session, document_id):
        return row

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)

    with pytest.raises(ConflictError):
        await knowledge_service.update_document(
            None, actor, organization_id, row.id, DocumentUpdate(title="New title")
        )


@pytest.mark.asyncio
async def test_update_document_denies_without_review_permission(monkeypatch) -> None:
    organization_id = uuid.uuid4()
    actor = _non_reviewer(organization_id)
    row = _FakeRow(organization_id=organization_id, status="proposed")

    async def fake_get_document_by_id(session, document_id):
        return row

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)

    with pytest.raises(PermissionDeniedError):
        await knowledge_service.update_document(
            None, actor, organization_id, row.id, DocumentUpdate(title="New title")
        )


@pytest.mark.asyncio
async def test_update_document_project_scoped_permission_denies_for_different_project(
    monkeypatch,
) -> None:
    """A reviewer holding `knowledge:review` only on a *different* project
    must be denied -- confirms `update_document` (like the now-project-scoped
    `publish_document`/`reject_document`) checks the permission against the
    document's own `project_id`, not just organization membership.
    """
    organization_id = uuid.uuid4()
    document_project_id = uuid.uuid4()
    other_project_id = uuid.uuid4()
    actor = Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id,
        user_id=uuid.uuid4(),
        project_permissions={other_project_id: frozenset({"knowledge:review"})},
    )
    row = _FakeRow(
        organization_id=organization_id, project_id=document_project_id, status="proposed"
    )

    async def fake_get_document_by_id(session, document_id):
        return row

    monkeypatch.setattr(knowledge_service.repository, "get_document_by_id", fake_get_document_by_id)

    with pytest.raises(PermissionDeniedError):
        await knowledge_service.update_document(
            None, actor, organization_id, row.id, DocumentUpdate(title="New title")
        )
