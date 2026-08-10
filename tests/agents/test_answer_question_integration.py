"""Integration-style tests for `app.agents.service.answer_question` (2026-08
audit "H3": zero automated coverage of the RAG answer pipeline).

Unlike `tests/agents/test_service.py` and `tests/agents/retrieval/
test_node.py` (which stub the Retrieval Agent's own dependencies to isolate
just that node), these tests run `answer_question` itself unmodified and let
the REAL compiled `answer_question` graph execute: query rewrite
(`agents.retrieval.rewriting`) -> hybrid retrieval + RRF fusion
(`retrieval.service.search`, real) -> cross-encoder reranking
(`agents.retrieval.reranking.rerank`, real) -> confidence evaluation
(`agents.confidence`, real) -> generation (`agents.answer.generation`, real)
-> grounding verification (`agents.answer.grounding`, real) -> citation
extraction (`agents.answer.citations`, real).

Only genuine I/O boundaries are faked, at the lowest layer each one has a
real seam for:
  - The `VectorStore` protocol (`app.retrieval.interfaces.base.VectorStore`)
    -- `retrieval.service`'s module-level `_store` singleton is swapped for
    a `_FakeVectorStore` implementing that same protocol, so the real
    `retrieval.service.search()` function (embedding + RRF fusion) still
    runs against it.
  - The embedding model (`app.retrieval.embedding`) -- swapped for a cheap,
    deterministic bag-of-words vector so grounding's cosine-similarity
    comparisons are controllable and fast, without downloading/running the
    real `sentence-transformers` model.
  - The cross-encoder reranking model (`agents.retrieval.reranking.
    _get_model`) -- same reasoning, a fake model with a controllable
    `predict()`.
  - The LLM (`agents.llm.get_llm`) -- a fake chat model that recognizes
    which of the three real prompts (query rewrite / generation / grounding
    escalation) it was given and answers accordingly. Patched at
    `app.agents.graph.get_llm`, not `app.agents.service.get_llm`: Phase 1's
    "Model routing (2.4)" feature moved every real `get_llm(task)` call for
    the `answer_question`/`triage_incident` graphs out of `agents.service`
    and into `agents.graph` (each node now resolves its own task-tier
    client at graph-build time -- see that module's docstring), so that is
    where this fake must now be installed. Every task this graph resolves
    (`"rewrite"`, `"generation"`, `"grounding_check"`) is patched to return
    this same one fake instance, exactly reproducing this suite's
    pre-existing behavior from before that feature (one shared `llm` for
    the whole graph run).
  - `agents.repository.insert_agent_execution`/`update_agent_execution` --
    real database writes, faked the same way `tests/agents/test_service.py`
    already does for this exact pair.

`answer_question` itself is never monkeypatched -- this is the difference
from `tests/api/test_ask_router.py`, which stubs it out entirely and
therefore never exercises any of the above.
"""

from __future__ import annotations

import math
import re
import uuid

import pytest

from app.agents import graph as graph_module
from app.agents import retry as retry_module
from app.agents import service as agents_service
from app.agents.answer.node import _INSUFFICIENT_GROUNDING_MESSAGE
from app.agents.retrieval import reranking as reranking_module
from app.retrieval import embedding as embedding_module
from app.retrieval import service as retrieval_service_module
from app.retrieval.schemas import ScoredChunk
from app.shared.schemas import ActorKind, Identity

# A small, deliberately curated "vocabulary" -- the fake embedding model
# below counts only these words, ignoring everything else in a text. This
# gives each test precise, readable control over resulting cosine
# similarities without needing a real embedding model or genuine
# multi-dimensional semantics (the same one-purpose-built-fake philosophy
# `tests/agents/answer/test_grounding.py` uses, scaled up to a small
# keyword-overlap scheme since this test also needs *retrieval* similarity
# signals to behave sensibly, not just grounding's).
_VOCAB = [
    "memory",
    "leak",
    "checkout",
    "crashes",
    "connection",
    "pool",
    "payment",
    "gateway",
    "unit",
    "tests",
]
_WORD_PATTERN = re.compile(r"[a-z]+")


def _bag_of_words_vector(text: str) -> list[float]:
    words = _WORD_PATTERN.findall(text.lower())
    counts = [float(words.count(word)) for word in _VOCAB]
    norm = math.sqrt(sum(c * c for c in counts))
    if norm == 0.0:
        return counts  # an all-zero vector -- has zero cosine similarity to everything
    return [c / norm for c in counts]


async def _fake_embed_query(query: str) -> list[float]:
    return _bag_of_words_vector(query)


async def _fake_embed_texts(texts: list[str]) -> list[list[float]]:
    return [_bag_of_words_vector(text) for text in texts]


class _FakeCrossEncoderModel:
    """Stands in for the real `sentence_transformers.CrossEncoder` --
    scores a (query, chunk_content) pair high if `high_score_keyword`
    appears in the content, low otherwise, so reranking's real sort logic
    has something meaningful (and known in advance) to reorder.
    """

    def __init__(self, high_score_keyword: str) -> None:
        self.high_score_keyword = high_score_keyword
        self.predict_calls = 0

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        self.predict_calls += 1
        return [5.0 if self.high_score_keyword in content else -5.0 for _query, content in pairs]


class _FakeVectorStore:
    """A `VectorStore`-shaped fake (see `app.retrieval.interfaces.base.
    VectorStore`) -- swapped in for `retrieval.service`'s real
    `PgVectorStore` singleton so the real `retrieval.service.search()`
    function's own logic (querying every collection, fusing dense +
    lexical results via RRF) still runs, against canned per-collection
    results instead of a real Postgres query.
    """

    def __init__(self, chunk_a: ScoredChunk, chunk_b: ScoredChunk) -> None:
        self._chunk_a = chunk_a
        self._chunk_b = chunk_b
        self.search_calls: list[str] = []

    async def search(self, session, collection, query_embedding, filters, top_k, *, include_metadata=False):
        self.search_calls.append(f"dense:{collection}")
        return self._results_for(collection)

    async def lexical_search(self, session, collection, query_text, filters, top_k, *, include_metadata=False):
        self.search_calls.append(f"lexical:{collection}")
        return self._results_for(collection)

    def _results_for(self, collection: str) -> list[ScoredChunk]:
        if collection == "documentation":
            return [self._chunk_a]
        if collection == "code":
            return [self._chunk_b]
        return []

    async def upsert(self, *args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("answer_question must never write to the vector store")

    async def delete(self, *args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("answer_question must never delete from the vector store")


class _FakeChatResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeAnswerLLM:
    """Recognizes which of the three real prompts this graph can produce
    (query rewrite, generation, grounding's ambiguous-band escalation) by
    its distinctive opening/contents, and answers accordingly -- an
    unrecognized prompt is a real bug (either in this fake or in a prompt
    template changing shape) and raises loudly rather than returning a
    silently-wrong canned response.
    """

    def __init__(self, generation_text: str) -> None:
        self.generation_text = generation_text
        self.rewrite_calls = 0
        self.generation_calls = 0
        self.grounding_check_calls = 0

    async def ainvoke(self, prompt: str) -> _FakeChatResponse:
        if prompt.startswith("Rewrite the following question"):
            self.rewrite_calls += 1
            return _FakeChatResponse(
                "checkout service crashes memory leak connection pool root cause"
            )
        if prompt.startswith("You are answering an engineer's question"):
            self.generation_calls += 1
            return _FakeChatResponse(self.generation_text)
        if "Is this claim directly supported" in prompt:
            self.grounding_check_calls += 1
            return _FakeChatResponse("yes")
        raise AssertionError(f"unexpected prompt reached the fake LLM: {prompt[:120]!r}")


class _FakeExecutionRow:
    def __init__(self, execution_id: uuid.UUID) -> None:
        self.id = execution_id


def _actor(organization_id: uuid.UUID | None = None) -> Identity:
    return Identity(
        kind=ActorKind.USER,
        subject=str(uuid.uuid4()),
        organization_id=organization_id or uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def _build_chunks() -> tuple[ScoredChunk, ScoredChunk]:
    # Deliberately worded identically to `_FakeAnswerLLM`'s happy-path
    # generation text (minus the citation marker) -- see that test's own
    # comment on why this guarantees a high grounding similarity without
    # needing a real embedding model to judge genuine semantic closeness.
    relevant_content = "The checkout service crashes due to a memory leak in the connection pool."
    irrelevant_content = "Unit tests for the payment gateway were updated recently."
    chunk_a = ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection="documentation",
        content=relevant_content,
        score=0.0,
        source_offset_start=0,
        source_offset_end=len(relevant_content),
        title="Checkout Incident Runbook",
        source_url="https://wiki.example.com/checkout-runbook",
    )
    chunk_b = ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection="code",
        content=irrelevant_content,
        score=0.0,
        source_offset_start=0,
        source_offset_end=len(irrelevant_content),
        title="payment_gateway_test.py",
        source_url="https://github.com/acme/widgets/blob/main/tests/payment_gateway_test.py",
    )
    return chunk_a, chunk_b


def _patch_common_dependencies(monkeypatch, *, fake_llm: _FakeAnswerLLM, recorded: dict[str, object]):
    """Wires every real I/O boundary the graph touches to a controlled
    fake -- see module docstring for the full list and rationale. Returns
    `(chunk_a, chunk_b, fake_store, fake_cross_encoder)` for tests that need
    to assert against them directly.
    """
    chunk_a, chunk_b = _build_chunks()
    fake_store = _FakeVectorStore(chunk_a, chunk_b)
    monkeypatch.setattr(retrieval_service_module, "_store", fake_store)
    monkeypatch.setattr(embedding_module, "embed_query", _fake_embed_query)
    monkeypatch.setattr(embedding_module, "embed_texts", _fake_embed_texts)

    fake_cross_encoder = _FakeCrossEncoderModel(high_score_keyword="memory leak")
    monkeypatch.setattr(reranking_module, "_get_model", lambda: fake_cross_encoder)

    # Model routing (Advanced Features Roadmap Phase 1, 2.4): `agents.graph.
    # build_graph` now calls `get_llm("rewrite")`/`get_llm("generation")`/
    # `get_llm("grounding_check")` itself, not `agents.service` -- patch it
    # there (see module docstring above for why).
    monkeypatch.setattr(graph_module, "get_llm", lambda *args, **kwargs: fake_llm)

    execution_id = uuid.uuid4()

    async def fake_insert_agent_execution(session, **kwargs):
        recorded["insert"] = kwargs
        return _FakeExecutionRow(execution_id)

    async def fake_update_agent_execution(session, exec_id, **kwargs):
        recorded["update"] = {"id": exec_id, **kwargs}

    monkeypatch.setattr(agents_service.repository, "insert_agent_execution", fake_insert_agent_execution)
    monkeypatch.setattr(agents_service.repository, "update_agent_execution", fake_update_agent_execution)

    return chunk_a, chunk_b, fake_store, fake_cross_encoder


@pytest.mark.asyncio
async def test_answer_question_executes_the_real_graph_end_to_end(monkeypatch) -> None:
    """The happy path, exercised for real end to end: a vague query forces a
    genuine query-rewrite LLM call; hybrid retrieval returns two chunks from
    two different collections; RRF fusion and reranking (real logic, fake
    models) both run and correctly prioritize the relevant chunk; confidence
    scores high enough to route to the Answer Agent; generation produces a
    marker-bearing answer; grounding verification (real logic, fake
    embedding model) confirms it against the actual retrieved content; and
    citation extraction correctly maps the surviving `[1]` marker back to
    the chunk that earned it.
    """
    recorded: dict[str, object] = {}
    fake_llm = _FakeAnswerLLM(
        generation_text=(
            "The checkout service crashes due to a memory leak in the connection pool [1]."
        )
    )
    chunk_a, _chunk_b, fake_store, fake_cross_encoder = _patch_common_dependencies(
        monkeypatch, fake_llm=fake_llm, recorded=recorded
    )

    actor = _actor()
    response = await agents_service.answer_question(
        session=None,
        query="What caused this issue?",  # "this issue" is a vague reference -> forces rewrite
        incident_id=None,
        actor=actor,
    )

    assert response.route_taken == "answer"
    assert response.confidence >= 0.6  # the real (unmocked) `.env` threshold
    assert response.answer is not None
    assert "[1]" not in response.answer  # markers stripped before reaching the caller
    assert "memory leak" in response.answer
    assert len(response.citations) == 1
    assert response.citations[0].chunk_id == chunk_a.chunk_id
    assert response.citations[0].document_id == chunk_a.document_id
    assert response.citations[0].source_url == chunk_a.source_url

    # Every real stage actually ran -- not just a plausible-looking final
    # answer assembled some other way.
    assert fake_llm.rewrite_calls == 1  # query rewrite genuinely fired
    assert fake_llm.generation_calls == 1
    assert fake_cross_encoder.predict_calls >= 1  # reranking genuinely ran
    assert "dense:documentation" in fake_store.search_calls
    assert "lexical:documentation" in fake_store.search_calls
    assert "dense:code" in fake_store.search_calls

    assert recorded["update"]["status"] == "succeeded"
    assert recorded["update"]["confidence_score"] == response.confidence


@pytest.mark.asyncio
async def test_answer_question_falls_back_to_insufficient_grounding_after_exhausting_retries(
    monkeypatch,
) -> None:
    """Edge/failure-case regression: when every generated sentence fails
    grounding verification on every retry attempt, the real graph must
    still complete normally -- no unhandled exception escaping
    `answer_question` -- and return the Answer Agent's own documented
    fallback message, never a partially-fabricated answer and never a
    crashed request. `agents.retry.call_with_retry`'s exponential backoff
    sleeps are faked to a no-op so this test doesn't actually wait ~3 real
    seconds; the retry *count* behavior is unchanged.
    """
    recorded: dict[str, object] = {}
    fake_llm = _FakeAnswerLLM(
        generation_text="This response uses completely unrelated wording every single time [1]."
    )
    _patch_common_dependencies(monkeypatch, fake_llm=fake_llm, recorded=recorded)

    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(retry_module.asyncio, "sleep", fake_sleep)

    actor = _actor()
    response = await agents_service.answer_question(
        session=None,
        query="What caused this issue?",
        incident_id=None,
        actor=actor,
    )

    assert response.route_taken == "answer"  # confidence was still high; only generation failed
    assert response.answer == _INSUFFICIENT_GROUNDING_MESSAGE
    assert response.citations == []
    # Initial attempt + 2 retries (`agents.retry._MAX_RETRIES == 2`) -- the
    # retry budget was genuinely exhausted, not short-circuited early or
    # retried indefinitely.
    assert fake_llm.generation_calls == 3
    # A documented, graceful degradation is still a *successful* agent
    # execution, not a failed one -- `agents.service`'s own module docstring
    # is explicit that only `EKIPError`/unexpected exceptions/a missing
    # `result` are marked `failed`.
    assert recorded["update"]["status"] == "succeeded"
