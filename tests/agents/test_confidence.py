"""Unit tests for `app.agents.confidence` (2026-08 audit "H3": zero
automated coverage of the RAG answer pipeline).

`evaluate_confidence` is pure -- no LLM call, no database/vector-store/
network I/O (see that module's own docstring) -- so every test here builds a
bare `GraphState` directly and asserts on the returned partial-state update.
No monkeypatching of `app.agents.confidence` itself is needed; the only
external dependency is `get_settings().confidence_threshold`, which is real
`.env` configuration (`CONFIDENCE_THRESHOLD=0.6`), not mocked here -- the
routing tests below pick signal values deliberately far from that threshold
in either direction so they aren't sensitive to its exact value.
"""

from __future__ import annotations

import math
import uuid

import pytest

from app.agents.confidence import (
    _MAX_POSSIBLE_FUSED_SCORE,
    _SOURCE_COUNT_CAP,
    confidence_evaluation_node,
    evaluate_confidence,
)
from app.agents.graph import GraphState
from app.retrieval.schemas import ScoredChunk
from app.shared.schemas import ActorKind, Identity


def _actor(organization_id: uuid.UUID | None = None) -> Identity:
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id or uuid.uuid4(),
    )


def _chunk(*, document_id: uuid.UUID | None = None, score: float = 0.0) -> ScoredChunk:
    content = "some retrieved content"
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        collection="documentation",
        content=content,
        score=score,
        source_offset_start=0,
        source_offset_end=len(content),
    )


def _state(**kwargs) -> GraphState:
    kwargs.setdefault("query", "how do I deploy the checkout service?")
    kwargs.setdefault("actor", _actor())
    return GraphState(**kwargs)


def test_no_chunks_and_no_signals_yields_zero_confidence_and_routes_to_investigation() -> None:
    state = _state(retrieved_chunks=[])

    update = evaluate_confidence(state)

    assert update["confidence_score"] == 0.0
    assert update["route"] == "investigation"
    assert update["confidence_signals"]["rerank_score"] == 0.0
    assert update["confidence_signals"]["source_count"] == 0.0


def test_top_similarity_signal_is_normalized_against_theoretical_max() -> None:
    """`top_similarity` is seeded by the Retrieval Agent as a raw fused RRF
    score, not a 0-1 value -- `evaluate_confidence` must normalize it via
    min-max against `_MAX_POSSIBLE_FUSED_SCORE` before it's usable alongside
    the other 0-1 signals.
    """
    state = _state(
        retrieved_chunks=[],
        confidence_signals={"top_similarity": _MAX_POSSIBLE_FUSED_SCORE / 2},
    )

    update = evaluate_confidence(state)

    assert update["confidence_signals"]["top_similarity"] == 0.5


def test_top_similarity_normalization_clamps_to_one_above_theoretical_max() -> None:
    """A defensive clamp, not a divide-and-trust: a raw fused score should
    never exceed the theoretical ceiling in practice, but if the fusion
    inputs (list count, `k`) ever drift out of lockstep with this constant,
    normalization must not silently produce a value above 1.0.
    """
    state = _state(
        retrieved_chunks=[],
        confidence_signals={"top_similarity": _MAX_POSSIBLE_FUSED_SCORE * 10},
    )

    update = evaluate_confidence(state)

    assert update["confidence_signals"]["top_similarity"] == 1.0


def test_top_similarity_normalization_clamps_to_zero_below_zero() -> None:
    state = _state(retrieved_chunks=[], confidence_signals={"top_similarity": -1.0})

    update = evaluate_confidence(state)

    assert update["confidence_signals"]["top_similarity"] == 0.0


def test_rerank_score_is_sigmoid_of_top_chunk_raw_score() -> None:
    """`rerank_score` reads `retrieved_chunks[0].score` -- the cross-encoder
    score `agents.retrieval.reranking` already wrote onto it -- and squashes
    the unbounded logit through a sigmoid.
    """
    raw_rerank_score = 2.0
    state = _state(retrieved_chunks=[_chunk(score=raw_rerank_score)])

    update = evaluate_confidence(state)

    expected = 1.0 / (1.0 + math.exp(-raw_rerank_score))
    assert update["confidence_signals"]["rerank_score"] == expected


def test_rerank_score_of_zero_chunks_is_zero_not_sigmoid_of_zero() -> None:
    """Zero retrieved chunks must not be scored the same as a genuine
    (very low-confidence) rerank score of exactly 0.0 -- `sigmoid(0) = 0.5`
    would otherwise make "found nothing" look like a middling-confidence
    result.
    """
    state = _state(retrieved_chunks=[])

    update = evaluate_confidence(state)

    assert update["confidence_signals"]["rerank_score"] == 0.0


def test_source_count_counts_distinct_documents_not_chunks() -> None:
    """AGENT_WORKFLOWS.md section 2.2: 'five chunks from one stale doc is
    weaker evidence than one chunk each from five sources' -- this signal
    must reward distinct documents, not raw chunk count.
    """
    same_document_id = uuid.uuid4()
    state = _state(
        retrieved_chunks=[_chunk(document_id=same_document_id) for _ in range(5)]
    )

    update = evaluate_confidence(state)

    assert update["confidence_signals"]["source_count"] == 1 / _SOURCE_COUNT_CAP


def test_source_count_caps_at_the_configured_ceiling() -> None:
    chunks = [_chunk(document_id=uuid.uuid4()) for _ in range(_SOURCE_COUNT_CAP + 5)]
    state = _state(retrieved_chunks=chunks)

    update = evaluate_confidence(state)

    assert update["confidence_signals"]["source_count"] == 1.0


def test_historical_similarity_is_dropped_for_non_triage_calls() -> None:
    """`historical_similarity` only applies to incident-triage calls
    (`state.incident_id is not None`) -- if some future caller ever seeds it
    for a plain `answer_question` call, it must be dropped, not silently
    included in the weighted average.
    """
    state = _state(
        incident_id=None,
        retrieved_chunks=[],
        confidence_signals={"historical_similarity": 0.9},
    )

    update = evaluate_confidence(state)

    assert "historical_similarity" not in update["confidence_signals"]


def test_historical_similarity_is_kept_for_triage_calls_when_seeded() -> None:
    state = _state(
        incident_id=uuid.uuid4(),
        retrieved_chunks=[],
        confidence_signals={"historical_similarity": 0.9},
    )

    update = evaluate_confidence(state)

    assert update["confidence_signals"]["historical_similarity"] == 0.9


def test_weighted_score_renormalizes_over_whichever_signals_are_present() -> None:
    """`historical_similarity`'s frequent absence (no incidents retrieval
    collection exists yet -- see module docstring) must not silently deflate
    every non-triage confidence score relative to a hypothetical score that
    included it. A perfect score on every *other* signal should still reach
    1.0, not `1.0 - historical_similarity's weight`.
    """
    document_id = uuid.uuid4()
    chunks = [_chunk(document_id=uuid.uuid4()) for _ in range(_SOURCE_COUNT_CAP)]
    # rerank_score reads chunks[0].score -- set it high enough that its
    # sigmoid is (for this test's purposes) close enough to 1.0.
    chunks[0] = chunks[0].model_copy(update={"score": 20.0})
    state = _state(
        incident_id=None,  # historical_similarity inapplicable -> dropped
        retrieved_chunks=chunks,
        confidence_signals={"top_similarity": _MAX_POSSIBLE_FUSED_SCORE},
    )
    _ = document_id

    update = evaluate_confidence(state)

    # sigmoid(20.0) is only *approximately* 1.0 in double precision
    # (~0.9999999979), not exactly -- `pytest.approx` avoids a flaky exact
    # float-equality assertion here.
    assert update["confidence_score"] == pytest.approx(1.0)


def test_route_is_answer_when_score_is_at_or_above_threshold() -> None:
    # top_similarity=1.0 (max), rerank_score's sigmoid(20) ~= 1.0,
    # source_count at cap -- comfortably above the real, unmocked 0.6
    # threshold (`.env`'s `CONFIDENCE_THRESHOLD=0.6`).
    chunks = [_chunk(document_id=uuid.uuid4(), score=20.0) for _ in range(_SOURCE_COUNT_CAP)]
    state = _state(
        retrieved_chunks=chunks,
        confidence_signals={"top_similarity": _MAX_POSSIBLE_FUSED_SCORE},
    )

    update = evaluate_confidence(state)

    assert update["confidence_score"] >= 0.6
    assert update["route"] == "answer"


def test_route_is_investigation_when_score_is_below_threshold() -> None:
    state = _state(retrieved_chunks=[], confidence_signals={})

    update = evaluate_confidence(state)

    assert update["confidence_score"] < 0.6
    assert update["route"] == "investigation"


def test_confidence_evaluation_node_is_a_trivial_wrapper_around_evaluate_confidence() -> None:
    """The LangGraph-callable node is a synchronous, no-I/O passthrough --
    this test guards against that wrapper ever growing hidden logic of its
    own that `evaluate_confidence`'s own tests wouldn't catch.
    """
    state = _state(retrieved_chunks=[_chunk(score=3.0)])

    assert confidence_evaluation_node(state) == evaluate_confidence(state)
