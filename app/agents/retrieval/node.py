"""The Retrieval Agent node (PROJECT_PLAN.md section 6.1 / AGENT_WORKFLOWS.md
section 2.1): turns a raw query into ranked, citation-anchored,
authorization-filtered evidence -- populates `GraphState.retrieved_chunks`
and `GraphState.rewritten_query`.

Owned by: agents/retrieval/ (the graph node -- distinct from `app/retrieval/`,
the storage-agnostic retrieval library it calls into; see PROJECT_PLAN.md
section 10's naming-collision note).

Built as a factory (`make_retrieval_agent_node`), not a bare module-level
function: this node needs a caller-scoped `AsyncSession` (the same "session
passed in, never opened internally" convention used everywhere else in this
codebase -- core/*, ingestion/*, retrieval/*) to call
`core.incidents.get_incident` and `retrieval.service.search`, but a
LangGraph node's own calling convention is fixed to `(state)`. The factory
closes over the request-scoped session (and the shared LLM client) once per
`answer_question`/`triage_incident` invocation (`agents/graph.py`, task #21),
producing the actual node callable LangGraph invokes.

Failure handling per AGENT_WORKFLOWS.md section 2.1: an embedding/vector-store
failure that exhausts `agents.retry.call_with_retry`'s retries does not raise
out of this node -- it degrades to `retrieved_chunks = []`, which is not an
error state; the Confidence Evaluation node (task #19) scores that as
effectively zero confidence and routes to Investigation, exactly the case
that route exists for.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import GraphState
from app.agents.retrieval.context_assembly import assemble_context
from app.agents.retrieval.reranking import rerank
from app.agents.retrieval.rewriting import rewrite_query
from app.agents.retry import call_with_retry
from app.retrieval import service as retrieval_service
from app.retrieval.schemas import SearchFilters
from app.shared.config.logging import get_logger

logger = get_logger(__name__)

# How many candidates hybrid search pulls before reranking narrows them --
# wider than the final context window, per PROJECT_PLAN.md section 5.3's
# two-stage pattern (cheap recall over a larger set, expensive precision
# over a smaller one).
_CANDIDATE_POOL_SIZE = 40
_RERANKED_TOP_K = 20


def make_retrieval_agent_node(
    session: AsyncSession, llm: BaseChatModel
) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
    """Build the LangGraph-callable Retrieval Agent node, bound to `session`
    and `llm` for the lifetime of one graph invocation.
    """

    async def node(state: GraphState) -> dict[str, Any]:
        rewritten_query = await rewrite_query(
            session,
            query=state.query,
            incident_id=state.incident_id,
            actor=state.actor,
            llm=llm,
            retry_count=state.retry_count,
        )

        # project_ids/permission_codes resolved from the actor's own
        # project-scoped grants (Identity.resolve_search_scope) rather than
        # left at "every project in the organization": a caller with no
        # project-scoped membership still searches unrestricted by project
        # (the org-level-only common case), but a caller who does hold one
        # or more project memberships is restricted to exactly those
        # projects, closing the cross-project leak an org-level permission
        # alone used to allow.
        project_ids, permission_codes = state.actor.resolve_search_scope()
        filters = SearchFilters(
            organization_id=state.actor.organization_id,
            project_ids=project_ids,
            permission_codes=permission_codes,
        )

        try:
            candidates = await call_with_retry(
                "retrieval_agent.hybrid_search",
                lambda: retrieval_service.search(
                    session, rewritten_query, filters, _CANDIDATE_POOL_SIZE
                ),
                retry_count=state.retry_count,
            )
        except Exception as exc:
            # Terminal condition, not an error -- see module docstring.
            logger.warning(
                "retrieval_agent_hybrid_search_exhausted",
                query=rewritten_query,
                error=str(exc),
            )
            candidates = []

        # Captured before `rerank()` overwrites each chunk's `.score` with
        # its cross-encoder score (reranking.py) -- the Confidence
        # Evaluation node (task #19) needs both signals separately
        # (AGENT_WORKFLOWS.md section 2.2 lists "top_similarity" and
        # "rerank_score" as two distinct signals, not the same value read
        # twice). This is the top candidate's *fused* retrieval score (RRF
        # over dense + lexical, across every collection -- `retrieval.
        # service.search()`'s return value), not a literal cosine/inner-
        # product similarity: `retrieval.service.search()` only exposes the
        # fused result, not each method's raw per-candidate score
        # separately. `agents.confidence`'s module docstring documents the
        # exact normalization applied to this value.
        top_fused_score = candidates[0].score if candidates else 0.0

        reranked = await rerank(rewritten_query, candidates, top_k=_RERANKED_TOP_K)
        assembled_chunks = assemble_context(reranked)

        return {
            "retrieved_chunks": assembled_chunks,
            "rewritten_query": rewritten_query,
            "retry_count": state.retry_count,
            "confidence_signals": {"top_similarity": top_fused_score},
        }

    return node
