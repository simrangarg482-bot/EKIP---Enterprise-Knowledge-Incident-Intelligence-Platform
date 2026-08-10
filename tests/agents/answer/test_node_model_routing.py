"""Unit tests for `app.agents.answer.node.make_answer_agent_node`'s model
routing (Advanced Features Roadmap Phase 1, "Model routing (2.4)").

Before this feature, `make_answer_agent_node` took one shared `llm` used for
both the drafting call (`generate_answer`) and the grounding-check
escalation call (`verify_grounding`) -- `app.agents.graph.build_graph` now
resolves two separately task-tiered clients (`get_llm("generation")` /
`get_llm("grounding_check")`) and this node must route each to the right
function. `generate_answer`/`verify_grounding` are monkeypatched at the
boundary `app.agents.answer.node` imports them through: this test's only
concern is *which* client each receives, not either function's own internal
logic (already covered by `tests/agents/answer/test_grounding.py`, and this
module's real generation-prompt logic needs no test double at all).
"""

from __future__ import annotations

import uuid

import pytest

from app.agents.answer import node as node_module
from app.agents.graph import GraphState
from app.retrieval.schemas import ScoredChunk
from app.shared.schemas import Identity


def _chunk() -> ScoredChunk:
    content = "The checkout service crashes due to a memory leak."
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        collection="documentation",
        content=content,
        score=0.9,
        source_offset_start=0,
        source_offset_end=len(content),
        title="Runbook",
    )


def _state(chunks: list[ScoredChunk]) -> GraphState:
    return GraphState(
        query="What caused this?",
        actor=Identity.for_agent("test", uuid.uuid4()),
        retrieved_chunks=chunks,
    )


class _SentinelLLM:
    """A distinct, identity-comparable stand-in for a `ChatOpenAI` client --
    these tests only ever check *which* sentinel a call received, never
    invoke it for real.
    """


@pytest.mark.asyncio
async def test_make_answer_agent_node_routes_generation_and_grounding_llms_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generation_llm = _SentinelLLM()
    grounding_llm = _SentinelLLM()
    generate_answer_calls: list[object] = []
    verify_grounding_calls: list[object] = []

    async def fake_generate_answer(llm: object, query: str, chunks: list[ScoredChunk]) -> str:
        generate_answer_calls.append(llm)
        return "The checkout service crashes due to a memory leak [1]."

    async def fake_verify_grounding(
        llm: object, sentences: list[str], chunks: list[ScoredChunk]
    ) -> list[str]:
        verify_grounding_calls.append(llm)
        return sentences

    monkeypatch.setattr(node_module, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(node_module, "verify_grounding", fake_verify_grounding)

    node = node_module.make_answer_agent_node(generation_llm, grounding_llm)
    chunks = [_chunk()]

    result = await node(_state(chunks))

    assert generate_answer_calls == [generation_llm]
    assert verify_grounding_calls == [grounding_llm]
    assert result["result"].answer == "The checkout service crashes due to a memory leak ."
    assert len(result["result"].citations) == 1
    assert result["result"].citations[0].chunk_id == chunks[0].chunk_id


@pytest.mark.asyncio
async def test_make_answer_agent_node_never_passes_grounding_llm_to_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cross-check on the same routing invariant: if the two clients were
    ever accidentally swapped or collapsed back into one shared instance,
    this asserts loudly rather than passing by coincidence.
    """
    generation_llm = _SentinelLLM()
    grounding_llm = _SentinelLLM()

    async def fake_generate_answer(llm: object, query: str, chunks: list[ScoredChunk]) -> str:
        assert llm is generation_llm
        assert llm is not grounding_llm
        return "Answer text [1]."

    async def fake_verify_grounding(
        llm: object, sentences: list[str], chunks: list[ScoredChunk]
    ) -> list[str]:
        assert llm is grounding_llm
        assert llm is not generation_llm
        return sentences

    monkeypatch.setattr(node_module, "generate_answer", fake_generate_answer)
    monkeypatch.setattr(node_module, "verify_grounding", fake_verify_grounding)

    node = node_module.make_answer_agent_node(generation_llm, grounding_llm)
    result = await node(_state([_chunk()]))

    assert result["result"].route_taken == "answer"
