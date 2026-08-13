"""pgvector-backed `VectorStore` implementation (PROJECT_PLAN.md section 5,
Milestone 5's "pgvector backend" bullet -- the only backend this milestone
builds; see `app.retrieval.interfaces.base`'s module docstring for why
Qdrant stays unbuilt for now).

Owned by: retrieval/pgvector/. Backs all three collections
(`documentation_chunks`, `code_chunks`, `conversations_chunks`) today --
`_COLLECTION_MODELS` is the only place that would need to change if a
future collection moved to the Qdrant backend instead (PROJECT_PLAN.md
section 8.4: a per-collection configuration choice, not a code fork).

Joins back to `documents` (ingestion-owned) for `title`/`source_url` at
query time -- the same direct-database-read exception already established
for ingestion reading `connector_configs` (`app.ingestion.repository`'s
module docstring) and anticipated by section 9.9's own dependency list:
"database (metadata joins), shared".
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.ingestion_models import Document, DocumentMetadata
from app.database.models.retrieval_models import CodeChunk, ConversationChunk, DocumentationChunk
from app.retrieval.schemas import CollectionName, ScoredChunk, SearchFilters, UpsertChunk

_COLLECTION_MODELS: dict[CollectionName, type] = {
    "documentation": DocumentationChunk,
    "code": CodeChunk,
    "conversations": ConversationChunk,
}


_REPO_SCOPED_COLLECTIONS: frozenset[CollectionName] = frozenset({"code", "documentation"})


def _repository_filter_clause(collection: CollectionName, model: type, repository: str):
    """`SearchFilters.repository`'s WHERE clause -- only `CodeChunk`/
    `DocumentationChunk` carry a `repo_full_name` column (real GitHub
    connector output lands in both -- source files in `"code"`, but
    issues/PRs/commit messages/READMEs, which is most of what a typical
    repo actually produces, in `"documentation"`). `ConversationChunk` has
    no such column, so filtering by repository there is a caller error, not
    a silent no-op.
    """
    if collection not in _REPO_SCOPED_COLLECTIONS:
        raise ValueError(
            f"SearchFilters.repository is only supported for the 'code' and "
            f"'documentation' collections, got collection={collection!r}."
        )
    return model.repo_full_name == repository


class PgVectorStore:
    """The pgvector `VectorStore` implementation. Stateless (holds no
    per-instance connection or config) -- every method takes the session
    and collection it needs, so one shared instance serves every collection
    and every caller's session.
    """

    async def search(
        self,
        session: AsyncSession,
        collection: CollectionName,
        query_embedding: list[float],
        filters: SearchFilters,
        top_k: int,
        *,
        include_metadata: bool = False,
    ) -> list[ScoredChunk]:
        """See `VectorStore.search`'s docstring for the contract.

        Uses pgvector's max-inner-product operator (`<#>`), not cosine
        distance (`<=>`): `retrieval.embedding` L2-normalizes every vector
        at generation time, so inner product and cosine similarity rank
        identically for normalized vectors, and inner product is the
        cheaper of the two to compute (see `embedding.py`'s docstring).
        pgvector's `max_inner_product` returns the *negated* inner product
        (so that, like its other distance operators, smaller means "more
        similar" -- consistent `ORDER BY ... ASC` semantics across every
        pgvector operator) -- negated back to a normal similarity score
        before returning.

        Tenant/project/ACL filters are applied as `WHERE` clauses on this
        query, never as a post-filter on already-fetched rows (PROJECT_PLAN.md
        sections 5.4-5.5).

        `include_metadata` is opt-in (default `False`) -- see
        `ScoredChunk.metadata`'s docstring for why this isn't joined
        unconditionally.
        """
        model = _COLLECTION_MODELS[collection]
        distance = model.embedding.max_inner_product(query_embedding)

        stmt = (
            select(
                model.id,
                model.document_id,
                model.content,
                model.source_offset_start,
                model.source_offset_end,
                distance.label("distance"),
                Document.title,
                Document.source_url,
            )
            .join(Document, Document.id == model.document_id)
            .where(model.organization_id == filters.organization_id)
            .where(
                or_(
                    model.acl_permission_code.is_(None),
                    model.acl_permission_code.in_(filters.permission_codes),
                )
            )
        )
        if filters.project_ids is not None:
            stmt = stmt.where(model.project_id.in_(filters.project_ids))
        if filters.repository is not None:
            stmt = stmt.where(_repository_filter_clause(collection, model, filters.repository))

        stmt = stmt.order_by(distance.asc()).limit(top_k)

        result = await session.execute(stmt)
        rows = result.all()
        metadata_by_document = await self._load_metadata_by_document(
            session, [row.document_id for row in rows] if include_metadata else []
        )
        return [
            ScoredChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                collection=collection,
                content=row.content,
                score=-row.distance,
                source_offset_start=row.source_offset_start,
                source_offset_end=row.source_offset_end,
                title=row.title,
                source_url=row.source_url,
                metadata=metadata_by_document.get(row.document_id, {}),
            )
            for row in rows
        ]

    async def lexical_search(
        self,
        session: AsyncSession,
        collection: CollectionName,
        query_text: str,
        filters: SearchFilters,
        top_k: int,
        *,
        include_metadata: bool = False,
    ) -> list[ScoredChunk]:
        """See `VectorStore.lexical_search`'s docstring for the contract.

        Postgres full-text search against the generated `content_tsv`
        column (`app.database.models.retrieval_models`'s module docstring
        explains why this is `ts_rank_cd`-based ranking, not literally
        BM25). `plainto_tsquery` (not `websearch_to_tsquery` or a raw
        `to_tsquery`) treats `query_text` as a plain phrase -- no operator
        syntax (`&`, `|`, `:*`) a caller would need to know about or escape.

        `include_metadata` -- see `search()`'s docstring, same contract.
        """
        model = _COLLECTION_MODELS[collection]
        tsquery = func.plainto_tsquery("english", query_text)
        rank = func.ts_rank_cd(model.content_tsv, tsquery).label("rank")

        stmt = (
            select(
                model.id,
                model.document_id,
                model.content,
                model.source_offset_start,
                model.source_offset_end,
                rank,
                Document.title,
                Document.source_url,
            )
            .join(Document, Document.id == model.document_id)
            .where(model.content_tsv.op("@@")(tsquery))
            .where(model.organization_id == filters.organization_id)
            .where(
                or_(
                    model.acl_permission_code.is_(None),
                    model.acl_permission_code.in_(filters.permission_codes),
                )
            )
        )
        if filters.project_ids is not None:
            stmt = stmt.where(model.project_id.in_(filters.project_ids))
        if filters.repository is not None:
            stmt = stmt.where(_repository_filter_clause(collection, model, filters.repository))

        stmt = stmt.order_by(rank.desc()).limit(top_k)

        result = await session.execute(stmt)
        rows = result.all()
        metadata_by_document = await self._load_metadata_by_document(
            session, [row.document_id for row in rows] if include_metadata else []
        )
        return [
            ScoredChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                collection=collection,
                content=row.content,
                score=row.rank,
                source_offset_start=row.source_offset_start,
                source_offset_end=row.source_offset_end,
                title=row.title,
                source_url=row.source_url,
                metadata=metadata_by_document.get(row.document_id, {}),
            )
            for row in rows
        ]

    async def _load_metadata_by_document(
        self, session: AsyncSession, document_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, str]]:
        """Fetch every `document_metadata` row for `document_ids` in one
        query, folded into `{document_id: {key: value, ...}, ...}`.

        One extra query per `search()`/`lexical_search()` call (not one per
        chunk) -- called only when `include_metadata=True`; `document_ids`
        is passed in already-empty by both callers otherwise, so this
        short-circuits without a round-trip.
        """
        if not document_ids:
            return {}

        stmt = select(
            DocumentMetadata.document_id, DocumentMetadata.key, DocumentMetadata.value
        ).where(DocumentMetadata.document_id.in_(set(document_ids)))
        result = await session.execute(stmt)

        metadata_by_document: dict[uuid.UUID, dict[str, str]] = {}
        for document_id, key, value in result.all():
            metadata_by_document.setdefault(document_id, {})[key] = value
        return metadata_by_document

    async def upsert(
        self,
        session: AsyncSession,
        collection: CollectionName,
        chunks: list[UpsertChunk],
        embeddings: list[list[float]],
    ) -> None:
        """See `VectorStore.upsert`'s docstring for the contract.

        One `INSERT ... ON CONFLICT (document_id, chunk_index) DO UPDATE`
        per chunk, targeting each table's `uq_<table>_document_chunk_index`
        unique constraint (`app.database.models.retrieval_models`) -- a
        real upsert, not a delete-then-insert, so re-embedding an unchanged
        chunk doesn't churn its primary key or briefly leave it absent from
        the index mid-write.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(
                f"chunks and embeddings must be the same length "
                f"(got {len(chunks)} chunks, {len(embeddings)} embeddings)."
            )

        model = _COLLECTION_MODELS[collection]
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            values = dict(
                organization_id=chunk.organization_id,
                project_id=chunk.project_id,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                embedding=embedding,
                source_offset_start=chunk.source_offset_start,
                source_offset_end=chunk.source_offset_end,
                acl_permission_code=chunk.acl_permission_code,
            )
            update_columns = [
                "organization_id",
                "project_id",
                "content",
                "embedding",
                "source_offset_start",
                "source_offset_end",
                "acl_permission_code",
            ]
            if collection in _REPO_SCOPED_COLLECTIONS:
                values["repo_full_name"] = chunk.repo_full_name
                update_columns.append("repo_full_name")

            stmt = pg_insert(model).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["document_id", "chunk_index"],
                set_={column: getattr(stmt.excluded, column) for column in update_columns},
            )
            await session.execute(stmt)
        await session.flush()

    async def delete(
        self, session: AsyncSession, collection: CollectionName, document_id: uuid.UUID
    ) -> None:
        """See `VectorStore.delete`'s docstring for the contract."""
        model = _COLLECTION_MODELS[collection]
        await session.execute(sql_delete(model).where(model.document_id == document_id))
        await session.flush()
