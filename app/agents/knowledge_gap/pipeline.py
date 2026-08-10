"""Knowledge Gap Agent pipeline (AGENT_WORKFLOWS.md section 2.6 /
PROJECT_PLAN.md section 6.6): fetch -> cluster -> synthesize -> resolve
action -> persist, run once per organization.

A linear pipeline, not a `StateGraph` -- see `app.agents.knowledge_gap`'s
own module docstring for why (the same reasoning `agents.postmortem.
pipeline` already established for an identically non-branching flow).

Idempotency across scheduled runs: without some way to recognize "this is
the same gap I already reported an hour ago," every run of this pipeline
would create a duplicate `GapReport` for the same underlying topic, and
`GET /knowledge/gaps` would fill up with noise. Before inserting a new
report, each surviving cluster's centroid is compared (cosine similarity)
against every currently-open report's stored `topic_embedding`; a close
enough match merges the newly-clustered execution ids into the existing
report instead of creating a new one. This is the concrete mechanism behind
"never auto-creates a document" staying meaningful over many runs, not just
on the first one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.knowledge_gap import repository
from app.agents.knowledge_gap.clustering import cluster_by_similarity, cosine_similarity
from app.agents.llm import log_llm_usage
from app.database.models.agent_models import AgentExecution, KnowledgeGapReport
from app.retrieval import embedding
from app.retrieval import service as retrieval_service
from app.retrieval.schemas import SearchFilters
from app.shared.config.logging import get_logger

logger = get_logger(__name__)

# Only `answer_question` executions carry a free-text `query` in
# `input_summary` -- `triage_incident`'s is `{"incident_id": ...}` (no query
# text to cluster on) and `generate_postmortem` doesn't record confidence at
# all (per that function's own docstring: every failure there is terminal,
# not confidence-scored). Scoping to one agent name is a deliberate,
# flagged choice, not an oversight.
_ANSWER_AGENT_NAME = "answer_question"

# A cluster's topic-embedding match against an existing open report must
# clear a *higher* bar than the clustering pass itself (0.82) uses to group
# individual queries -- merging two genuinely different topics into one
# report is a worse failure than occasionally creating a near-duplicate
# report a human then has to notice is redundant.
_MERGE_SIMILARITY_THRESHOLD = 0.9

# A `retrieval.search` hit against the "documentation" collection at or
# above this score is treated as "a closely related document already
# exists" (PROJECT_PLAN.md section 6.6: "determined by checking whether a
# documents row already exists on a closely related topic").
_DOCUMENT_MATCH_SCORE_THRESHOLD = 0.6


def _average(vectors: list[list[float]]) -> list[float]:
    n = len(vectors)
    dimension = len(vectors[0])
    return [sum(vector[d] for vector in vectors) / n for d in range(dimension)]


def _find_mergeable_report(
    centroid: list[float], existing_reports: list[KnowledgeGapReport]
) -> KnowledgeGapReport | None:
    best_report: KnowledgeGapReport | None = None
    best_similarity = -1.0
    for report in existing_reports:
        similarity = cosine_similarity(centroid, report.topic_embedding)
        if similarity > best_similarity:
            best_similarity = similarity
            best_report = report
    if best_report is not None and best_similarity >= _MERGE_SIMILARITY_THRESHOLD:
        return best_report
    return None


async def _synthesize_topic(llm: BaseChatModel, queries: list[str]) -> str:
    """Produce a short, human-readable topic label summarizing what this
    cluster of differently-worded low-confidence queries has in common.

    One LLM call per surviving cluster (not per query) -- clusters are
    already small in absolute terms (a handful to a few dozen per run), so
    this is cheap, and a synthesized label reads far better than picking one
    raw query verbatim as a stand-in for the whole group.
    """
    rendered = "\n".join(f"- {query}" for query in queries[:20])
    prompt = (
        "The following are real questions employees asked an internal "
        "knowledge assistant that it could not answer confidently. They are "
        "different wordings of what is likely the same underlying "
        "documentation gap.\n\n"
        f"{rendered}\n\n"
        "Respond with ONLY a short topic label (5-10 words) describing what "
        "documentation is missing or unclear -- no preamble, no quotes, no "
        "markdown. Example: 'How to configure checkout service retry "
        "limits'."
    )
    response = await llm.ainvoke(prompt)
    log_llm_usage("knowledge_gap", llm, response)
    return str(response.content).strip()


async def _resolve_suggested_action(
    session: AsyncSession, organization_id: uuid.UUID, topic: str
) -> tuple[str, uuid.UUID | None]:
    """Decide `suggested_action` (PROJECT_PLAN.md section 6.6: "new runbook
    vs. update existing document -- determined by checking whether a
    `documents` row already exists on a closely related topic").

    Searches the "documentation" collection specifically (not all
    collections): a gap gets filled by a *document*, not a Slack message or
    a code chunk, so only that collection is a candidate for "update this
    instead of writing a new one."
    """
    filters = SearchFilters(organization_id=organization_id)
    results = await retrieval_service.search(
        session, topic, filters, top_k=1, collection="documentation"
    )
    if results and results[0].score >= _DOCUMENT_MATCH_SCORE_THRESHOLD:
        return "update_existing", results[0].document_id
    return "new_runbook", None


async def detect_knowledge_gaps(
    session: AsyncSession,
    llm: BaseChatModel,
    organization_id: uuid.UUID,
    *,
    confidence_threshold: float,
    lookback: timedelta,
    min_cluster_size: int,
    similarity_threshold: float,
) -> list[KnowledgeGapReport]:
    """Run the full Knowledge Gap Agent pipeline for one organization,
    returning every gap report created or updated by this run (empty if no
    cluster reached `min_cluster_size`).
    """
    since = datetime.now(timezone.utc) - lookback
    executions = await repository.list_low_confidence_executions(
        session,
        organization_id,
        agent_name=_ANSWER_AGENT_NAME,
        max_confidence=confidence_threshold,
        since=since,
    )

    queryable: list[tuple[AgentExecution, str]] = [
        (execution, execution.input_summary["query"])
        for execution in executions
        if execution.input_summary and execution.input_summary.get("query")
    ]
    if len(queryable) < min_cluster_size:
        logger.info(
            "knowledge_gap_insufficient_data",
            organization_id=str(organization_id),
            candidate_count=len(queryable),
        )
        return []

    embeddings = await embedding.embed_texts([query for _, query in queryable])
    clusters = cluster_by_similarity(embeddings, similarity_threshold=similarity_threshold)

    existing_reports = list(await repository.list_open_gap_reports(session, organization_id))

    results: list[KnowledgeGapReport] = []
    for member_indices in clusters:
        if len(member_indices) < min_cluster_size:
            continue  # a one-off hard question, not a repeated gap

        cluster_executions = [queryable[i][0] for i in member_indices]
        cluster_queries = [queryable[i][1] for i in member_indices]
        cluster_embeddings = [embeddings[i] for i in member_indices]
        centroid = _average(cluster_embeddings)
        execution_ids = [str(execution.id) for execution in cluster_executions]

        mergeable = _find_mergeable_report(centroid, existing_reports)
        if mergeable is not None:
            merged_ids = sorted({*mergeable.supporting_execution_ids, *execution_ids})
            updated = await repository.update_gap_report_supporting_ids(
                session, mergeable.id, supporting_execution_ids=merged_ids
            )
            if updated is not None:
                results.append(updated)
                logger.info(
                    "knowledge_gap_report_merged",
                    gap_report_id=str(mergeable.id),
                    organization_id=str(organization_id),
                )
            continue

        topic = await _synthesize_topic(llm, cluster_queries)
        suggested_action, related_document_id = await _resolve_suggested_action(
            session, organization_id, topic
        )
        row = await repository.insert_gap_report(
            session,
            organization_id=organization_id,
            suggested_topic=topic,
            topic_embedding=centroid,
            supporting_execution_ids=execution_ids,
            suggested_action=suggested_action,
            related_document_id=related_document_id,
        )
        results.append(row)
        logger.info(
            "knowledge_gap_report_created",
            gap_report_id=str(row.id),
            organization_id=str(organization_id),
            suggested_action=suggested_action,
        )

    return results
