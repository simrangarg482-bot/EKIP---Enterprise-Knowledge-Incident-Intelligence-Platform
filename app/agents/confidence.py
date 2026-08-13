"""The Confidence Evaluation Node (AGENT_WORKFLOWS.md section 2.2 /
PROJECT_PLAN.md section 6.2): deterministic, no-LLM scoring that combines
retrieval signals into `confidence_score` and decides `route`.

Owned by: agents/. Unlike every other node in this graph, this one is pure
computation -- no LLM call, no database/vector-store/network I/O -- so it
has no retryable-failure path (`agents.retry` is not used here) and cannot
time out or fail transiently. AGENT_WORKFLOWS.md section 2.2's own framing:
"the only 'failure' is an eventually-tuned threshold being wrong, which is a
data/tuning problem, not a code-failure one."

Weights and the exact combination formula are a placeholder
(`ENGINEERING_DECISIONS.md`'s "Open" section: "Confidence-score formula and
threshold -- will be decided empirically once real retrieval data exists"),
not a tuned model. The load-bearing property here is architectural, not
numerical: this function is pure and unit-testable with synthetic
`confidence_signals` inputs, exactly as AGENT_WORKFLOWS.md section 2.2
requires.

**Signal sourcing, since not every signal is computed here:**
- `top_similarity` -- seeded into `state.confidence_signals` by the
  Retrieval Agent (`agents.retrieval.node`), *before* reranking overwrites
  each chunk's `.score`. It is the top candidate's *fused* RRF score
  (dense + lexical, across every collection), not a literal cosine/inner-
  product similarity -- `retrieval.service.search()` only returns the fused
  result, never each method's raw per-candidate score. Normalized to 0-1
  here via min-max against the theoretical maximum fused score a candidate
  could reach (see `_normalize_top_similarity`).
- `rerank_score` -- computed here from `state.retrieved_chunks[0].score`
  (the cross-encoder score `agents.retrieval.reranking` already wrote onto
  each chunk). Cross-encoder scores are unbounded logits, squashed through
  a sigmoid to land on the same 0-1 scale as the other signals.
- `source_count` -- computed here: number of *distinct documents* (not
  chunks) in `state.retrieved_chunks`, normalized by `_SOURCE_COUNT_CAP`.
- `historical_similarity` -- for incident-triage calls only, per
  AGENT_WORKFLOWS.md. A real, flagged gap, not a silent omission: computing
  it means searching an "incidents" retrieval collection, and
  `app.database.models.retrieval_models`'s own module docstring already
  documents that no such collection exists yet ("nothing in core/incidents
  produces embeddable chunks for it today"). Since this node does no I/O of
  its own, it cannot fetch this signal even once that collection exists --
  some upstream node would need to supply it, the same way the Retrieval
  Agent now seeds `top_similarity`. Until then, this signal is simply
  absent from the weighted formula rather than fabricated as 0.0 --
  `_weighted_score` renormalizes over whichever signals are actually
  present, so its permanent absence today doesn't silently deflate every
  incident-triage confidence score relative to a non-triage one.
"""

from __future__ import annotations

import math
from typing import Any, Literal

from app.agents.graph import GraphState
from app.retrieval.schemas import ScoredChunk
from app.shared.config.logging import get_logger
from app.shared.config.settings import get_settings

logger = get_logger(__name__)

# Placeholder relative weights (see module docstring) -- not independently
# meaningful magnitudes, and renormalized over whichever signals are
# actually present for a given call (`_weighted_score`).
_SIGNAL_WEIGHTS: dict[str, float] = {
    "top_similarity": 0.40,
    "rerank_score": 0.35,
    "source_count": 0.15,
    "historical_similarity": 0.10,
}

# Distinct-document count beyond which additional sources stop adding
# confidence (AGENT_WORKFLOWS.md section 2.2: "five chunks from one stale
# doc is weaker evidence than one chunk each from five sources" -- this
# signal rewards distinctness, not an unbounded raw count).
_SOURCE_COUNT_CAP = 5

# Must match `app.retrieval.ranking.fusion._DEFAULT_K` -- duplicated rather
# than imported, same "cross-module constant, documented not shared"
# precedent as `retrieval.embedding.EMBEDDING_DIMENSION` duplicating
# `retrieval_models._EMBEDDING_DIMENSION` (that one for a leaf-module import
# restriction; this one to avoid reaching into another module's private
# constant for a single derived value).
_RRF_K = 60
# `retrieval.service.search()` runs both `search()` (dense) and
# `lexical_search()` (lexical) for each of the 3 collections -- 6 ranked
# lists feed reciprocal rank fusion in total, but any single chunk lives in
# exactly one collection's `<collection>_chunks` table, so it can only ever
# appear in *its own* collection's 2 lists (dense + lexical), never all 6.
# The theoretical ceiling for one candidate's fused score is therefore both
# of those 2 lists ranking it #1: `2 * (1 / (k + 1))`.
_MAX_POSSIBLE_FUSED_SCORE = 2 / (_RRF_K + 1)


def evaluate_confidence(state: GraphState) -> dict[str, Any]:
    """Pure function: `GraphState` in, a partial-state update out. The
    LangGraph-callable node (`confidence_evaluation_node` below) is a
    trivial synchronous wrapper around this, kept separate so the scoring
    logic itself stays unit-testable with a bare `GraphState` and no
    LangGraph machinery involved.
    """
    chunks = state.retrieved_chunks

    # Carries "top_similarity", seeded by the Retrieval Agent -- see module
    # docstring.
    signals = dict(state.confidence_signals)
    if "top_similarity" in signals:
        signals["top_similarity"] = _normalize_top_similarity(signals["top_similarity"])

    signals["rerank_score"] = _normalize_rerank_score(chunks[0].score) if chunks else 0.0
    signals["source_count"] = _distinct_source_count_signal(chunks)

    if state.incident_id is None:
        # historical_similarity only applies to incident-triage calls
        # (AGENT_WORKFLOWS.md section 2.2) -- drop it if some future caller
        # ever seeds it for a non-triage query.
        signals.pop("historical_similarity", None)
    # Else: left as whatever (if anything) an upstream node already put in
    # `state.confidence_signals` -- nothing does yet, see module docstring.

    confidence_score = _weighted_score(signals)
    # `confidence_threshold`'s default is evaluated, not guessed -- see
    # `scripts/eval_confidence.py` and the evidence comment on this field in
    # `app/shared/config/settings.py` before changing it.
    threshold = get_settings().confidence_threshold
    route: Literal["answer", "investigation"] = (
        "answer" if confidence_score >= threshold else "investigation"
    )

    logger.info(
        "confidence_evaluated",
        confidence_score=confidence_score,
        threshold=threshold,
        route=route,
        signals=signals,
    )

    return {
        "confidence_score": confidence_score,
        "confidence_signals": signals,
        "route": route,
    }


def confidence_evaluation_node(state: GraphState) -> dict[str, Any]:
    """The LangGraph-callable node. Synchronous (not `async def`, unlike
    every other node in this graph) -- an honest reflection of this node
    doing no I/O, per its own documented "no retryable failures" property.
    """
    return evaluate_confidence(state)


def _normalize_top_similarity(raw_fused_score: float) -> float:
    """Min-max normalize a fused RRF score to 0-1 against the theoretical
    maximum a candidate could reach (see `_MAX_POSSIBLE_FUSED_SCORE`).
    Clamped, not just divided: a chunk cannot exceed the theoretical
    ceiling in practice, but clamping guards against drift if the fusion
    inputs (list count, `k`) ever change without this constant being
    updated in lockstep.
    """
    return max(0.0, min(1.0, raw_fused_score / _MAX_POSSIBLE_FUSED_SCORE))


def _normalize_rerank_score(raw_score: float) -> float:
    """Cross-encoder scores (`agents.retrieval.reranking`) are unbounded
    logits, not a 0-1 similarity -- squash through a sigmoid so this signal
    sits on a comparable scale to the other 0-1 signals before weighting.
    """
    return 1.0 / (1.0 + math.exp(-raw_score))


def _distinct_source_count_signal(chunks: list[ScoredChunk]) -> float:
    """Number of *distinct documents* represented in `chunks` (not chunk
    count -- AGENT_WORKFLOWS.md section 2.2's own distinction), normalized
    to 0-1 by `_SOURCE_COUNT_CAP`.
    """
    distinct_documents = {chunk.document_id for chunk in chunks}
    return min(len(distinct_documents) / _SOURCE_COUNT_CAP, 1.0)


def _weighted_score(signals: dict[str, float]) -> float:
    """Weighted average over whichever signals are present in `signals`,
    renormalizing `_SIGNAL_WEIGHTS` to sum to 1 over just those keys -- see
    module docstring on why `historical_similarity`'s frequent absence must
    not silently deflate every score computed without it.
    """
    applicable_weights = {
        key: weight for key, weight in _SIGNAL_WEIGHTS.items() if key in signals
    }
    if not applicable_weights:
        return 0.0
    total_weight = sum(applicable_weights.values())
    return (
        sum(signals[key] * weight for key, weight in applicable_weights.items()) / total_weight
    )
