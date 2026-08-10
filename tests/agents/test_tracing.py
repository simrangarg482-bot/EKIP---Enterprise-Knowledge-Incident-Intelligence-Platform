"""Unit tests for `app.agents.tracing.traced_node` (Advanced Features
Roadmap Phase 1, "OTel tracing (2.3)").

Rather than relying on the process-global `opentelemetry.trace.
set_tracer_provider` singleton (which the OTel SDK only allows to be set
successfully once per process -- a second call elsewhere in the same test
run would silently no-op and leave these tests observing nothing), these
tests monkeypatch `app.agents.tracing._tracer` directly to a tracer bound to
a fresh, test-local `TracerProvider` wired to an `InMemorySpanExporter` --
both ship inside `opentelemetry-sdk` itself (already a declared dependency),
no extra test-utils package needed. This works because `_tracer` is looked
up as a module-global at call time inside `traced_node`'s closures, not
captured at function-definition time.
"""

from __future__ import annotations

import uuid

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.agents import tracing
from app.agents.graph import GraphState
from app.shared.schemas import Identity


@pytest.fixture()
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("test"))
    return exporter


def _state() -> GraphState:
    return GraphState(query="How do checkout 500s get handled?", actor=Identity.for_agent("test", uuid.uuid4()))


@pytest.mark.asyncio
async def test_traced_node_wraps_an_async_node_unchanged(span_exporter: InMemorySpanExporter) -> None:
    async def node(state: GraphState) -> dict:
        return {"retrieved_chunks": [], "rewritten_query": "rewritten"}

    wrapped = tracing.traced_node(
        "retrieval_agent",
        node,
        attributes_fn=lambda _s, update: {
            "retrieval.chunk_count": len(update["retrieved_chunks"]),
            "retrieval.rewritten": bool(update["rewritten_query"]),
        },
    )

    result = await wrapped(_state())

    assert result == {"retrieved_chunks": [], "rewritten_query": "rewritten"}

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "agent.retrieval_agent"
    assert spans[0].attributes["agent.node_name"] == "retrieval_agent"
    assert spans[0].attributes["retrieval.chunk_count"] == 0
    assert spans[0].attributes["retrieval.rewritten"] is True


def test_traced_node_wraps_a_sync_node_unchanged(span_exporter: InMemorySpanExporter) -> None:
    def node(state: GraphState) -> dict:
        return {"confidence_score": 0.87, "route": "answer"}

    wrapped = tracing.traced_node(
        "confidence_evaluation",
        node,
        attributes_fn=lambda _s, update: {
            "confidence.score": update["confidence_score"],
            "confidence.route": update["route"],
        },
    )

    result = wrapped(_state())

    assert result == {"confidence_score": 0.87, "route": "answer"}
    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "agent.confidence_evaluation"
    assert spans[0].attributes["confidence.score"] == 0.87
    assert spans[0].attributes["confidence.route"] == "answer"


def test_traced_node_preserves_sync_vs_async_calling_convention(span_exporter: InMemorySpanExporter) -> None:
    """LangGraph decides whether to `await` a node by inspecting the
    callable itself -- `traced_node` must not turn a sync node async or
    vice versa.
    """
    import inspect

    def sync_node(state: GraphState) -> dict:
        return {}

    async def async_node(state: GraphState) -> dict:
        return {}

    wrapped_sync = tracing.traced_node("x", sync_node)
    wrapped_async = tracing.traced_node("y", async_node)

    assert not inspect.iscoroutinefunction(wrapped_sync)
    assert inspect.iscoroutinefunction(wrapped_async)


@pytest.mark.asyncio
async def test_traced_node_records_exception_and_reraises_unchanged(
    span_exporter: InMemorySpanExporter,
) -> None:
    async def node(state: GraphState) -> dict:
        raise RuntimeError("retrieval backend unreachable")

    wrapped = tracing.traced_node("retrieval_agent", node)

    with pytest.raises(RuntimeError, match="retrieval backend unreachable"):
        await wrapped(_state())

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.status.status_code == StatusCode.ERROR
    assert len(span.events) == 1
    assert span.events[0].name == "exception"


def test_traced_node_sync_exception_recorded_and_reraised(span_exporter: InMemorySpanExporter) -> None:
    def node(state: GraphState) -> dict:
        raise ValueError("bad state")

    wrapped = tracing.traced_node("confidence_evaluation", node)

    with pytest.raises(ValueError, match="bad state"):
        wrapped(_state())

    spans = span_exporter.get_finished_spans()
    assert spans[0].status.status_code == StatusCode.ERROR


def test_traced_node_attributes_fn_not_called_on_exception(span_exporter: InMemorySpanExporter) -> None:
    calls: list[object] = []

    def node(state: GraphState) -> dict:
        raise RuntimeError("boom")

    def attributes_fn(state: GraphState, update: dict) -> dict:
        calls.append(update)
        return {}

    wrapped = tracing.traced_node("x", node, attributes_fn=attributes_fn)

    with pytest.raises(RuntimeError):
        wrapped(_state())

    assert calls == []


def test_traced_node_without_attributes_fn_still_sets_node_name(
    span_exporter: InMemorySpanExporter,
) -> None:
    def node(state: GraphState) -> dict:
        return {}

    wrapped = tracing.traced_node("confidence_evaluation", node)
    wrapped(_state())

    spans = span_exporter.get_finished_spans()
    assert spans[0].attributes["agent.node_name"] == "confidence_evaluation"


def test_traced_node_skips_none_valued_attributes(span_exporter: InMemorySpanExporter) -> None:
    def node(state: GraphState) -> dict:
        return {}

    wrapped = tracing.traced_node(
        "x", node, attributes_fn=lambda _s, _u: {"present": "value", "absent": None}
    )
    wrapped(_state())

    spans = span_exporter.get_finished_spans()
    assert spans[0].attributes["present"] == "value"
    assert "absent" not in spans[0].attributes
