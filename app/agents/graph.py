"""The single LangGraph state schema threaded through every node, plus the
actual `StateGraph` node wiring and compiled graph -- per the file layout
PROJECT_PLAN.md section 10 lays out: "graph.py -- state schema + node wiring
(the composing layer)".

Owned by: agents/. `GraphState` is defined here, not in `agents/schemas.py`
or `shared/schemas/`, because it is purely an internal wiring detail of this
one graph -- no other module ever sees a `GraphState` instance; every
caller-facing shape it eventually produces (`AskResponse`) is a plain,
already-shared type.

Per AGENT_WORKFLOWS.md section 1, this is one typed object carrying the
query, retrieved evidence, confidence score, and resolved `Identity` through
every node -- never a raw dict, so every node's inputs/outputs are checked
by the type system rather than by convention.

`evidence`/`hypotheses` are populated by the Investigation Agent
(`agents.investigation.node`, Milestone 7, task #23) -- this is the one
shared state object for the whole graph (investigation included), not a
Milestone-6-only subset that would need a breaking change to extend later.

**Graph wiring (task #21, real Investigation Agent wired in task #23):**
`build_graph(session)` composes Retrieval Agent -> Confidence
Evaluation -> a conditional edge on `state.route` -> Answer Agent (when
`route == "answer"`) or the Investigation Agent (when
`route == "investigation"`, `agents.investigation.node`). Rebuilt and
recompiled on every call rather than once at import time: `session` is
request-scoped (the same reasoning `agents.retrieval.node`'s module
docstring gives for its own factory pattern), so nodes closing over it
cannot be shared across requests; graph compilation itself has no
meaningful cost that would make caching worth the complexity.

**`build_investigation_graph(session)`** (task #23) is a second,
separate compiled graph containing only the Investigation Agent node --
built for `agents.service.triage_incident`, which enters directly at the
Investigation Agent per AGENT_WORKFLOWS.md section 11.3's request-flow
diagram, bypassing Retrieval Agent/Confidence Evaluation entirely (triage
always investigates, unlike `answer_question`'s confidence-routed path).
A separate graph, not `build_graph` re-entered mid-way: forcing
`build_graph`'s conditional edge down the investigation branch would
require faking a `confidence_score`/`route` on the initial state for a
stage that never actually ran, which is worse than just not running that
stage's edges at all.

**OTel tracing (Advanced Features Roadmap Phase 1, "OTel tracing (2.3)"):**
every node registered below is wrapped in `agents.tracing.traced_node`,
giving each graph run a full span tree (`agent.retrieval_agent` ->
`agent.confidence_evaluation` -> `agent.answer_agent`/
`agent.investigation_agent`) with a few node-specific attributes each --
see `agents/tracing.py`'s module docstring for why the wrapping happens
here, at each node's registration call site, rather than inside any node's
own file.

**Model routing (Advanced Features Roadmap Phase 1, "Model routing (2.4)"):**
neither `build_graph` nor `build_investigation_graph` takes an `llm`
parameter anymore (both did, before this feature) -- per-task model
routing (`app.agents.llm.get_llm(task)`) only has an effect if different
nodes can end up on genuinely different `ChatOpenAI` instances within the
same request, which one shared instance passed in from the caller
(`agents.service`) could never provide. Instead, this module -- already
the graph's "composing layer" per its own module docstring above -- is
where each node's own task-tier client is resolved, right at the point
each node is registered:

  - `retrieval_agent` gets `get_llm("rewrite")` (`agents.retrieval.
    rewriting`'s one LLM call).
  - `answer_agent` gets *two* clients: `get_llm("generation")` for the
    actual answer-drafting call and `get_llm("grounding_check")` for the
    grounding-verification escalation path -- previously one shared `llm`
    served both; see `agents.answer.node.make_answer_agent_node`'s own
    docstring for why that node's signature changed to take both.
  - `investigation_agent` gets `get_llm("hypothesis")`.
  - `confidence_evaluation` gets no client at all -- it never calls an LLM
    (see `app.agents.llm`'s module docstring on why "confidence" is not a
    routed task).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import get_llm
from app.agents.tracing import traced_node
from app.retrieval.schemas import ScoredChunk
from app.shared.schemas import AskResponse, EvidenceItem, Identity, RootCauseHypothesis


class GraphState(BaseModel):
    """State threaded through every node of the `answer_question` /
    `triage_incident` graph (AGENT_WORKFLOWS.md section 1).

    Not frozen: unlike the value objects it carries (`Identity`,
    `ScoredChunk`, ...), this is a mutable working document nodes
    incrementally populate as the graph advances -- each node returns the
    fields it updates, which LangGraph merges into the state passed to the
    next node.
    """

    # --- input ---------------------------------------------------------
    query: str
    incident_id: uuid.UUID | None = None
    actor: Identity

    # --- retrieval stage -------------------------------------------------
    retrieved_chunks: list[ScoredChunk] = Field(default_factory=list)
    rewritten_query: str | None = None

    # --- confidence stage --------------------------------------------------
    confidence_score: float | None = None
    # Kept for observability, not just the final number -- "why did this get
    # routed to investigation?" must be answerable from stored state, per
    # this field's rationale in AGENT_WORKFLOWS.md section 1.
    confidence_signals: dict[str, float] = Field(default_factory=dict)

    # --- routing ------------------------------------------------------------
    route: Literal["answer", "investigation"] | None = None

    # --- investigation stage (Milestone 7 -- see module docstring) ----------
    evidence: list[EvidenceItem] = Field(default_factory=list)
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)

    # --- output --------------------------------------------------------
    result: AskResponse | None = None

    # --- control -------------------------------------------------------
    # Per-node retry tracking (AGENT_WORKFLOWS.md section 4: up to 2 retries
    # per node with exponential backoff before converting to that node's
    # terminal-condition behavior).
    retry_count: dict[str, int] = Field(default_factory=dict)
    terminal_error: str | None = None


def _route_after_confidence(state: GraphState) -> str:
    """Conditional-edge selector run after `confidence_evaluation_node`."""
    return "answer" if state.route == "answer" else "investigation"


def _answer_agent_span_attributes(
    _state: GraphState, update: dict[str, Any]
) -> dict[str, str | int | float | bool]:
    """Span attributes for the `answer_agent` node -- `traced_node`'s
    `attributes_fn` for this node (see `agents/tracing.py`'s module
    docstring for why this extraction lives here, not inside
    `agents/answer/node.py` itself).
    """
    result = update.get("result")
    if result is None:
        return {}
    return {
        "answer.route_taken": result.route_taken,
        "answer.citation_count": len(result.citations),
    }


def _investigation_agent_span_attributes(
    _state: GraphState, update: dict[str, Any]
) -> dict[str, str | int | float | bool]:
    """Span attributes for the `investigation_agent` node -- see
    `_answer_agent_span_attributes`'s docstring for the same rationale.
    """
    result = update.get("result")
    if result is None or result.investigation is None:
        return {}
    return {
        "investigation.evidence_count": len(result.investigation.evidence),
        "investigation.hypothesis_count": len(result.investigation.hypotheses),
    }


def build_graph(session: AsyncSession) -> Any:
    """Compose and compile the graph described in this module's docstring,
    bound to `session` for the lifetime of one invocation. Returns a
    LangGraph `CompiledStateGraph` (left untyped here -- LangGraph does not
    export a stable public type name for it across the pinned version range).

    Resolves each node's own task-tier LLM client here (see module
    docstring's "Model routing" section) rather than accepting one shared
    `llm` parameter, as this function did before Phase 1's model-routing
    feature.

    The node-building imports below are deliberately local to this function,
    not module-level: `agents.answer.node`, `agents.confidence`,
    `agents.retrieval.node`, and `agents.investigation.node` each import
    `GraphState` *from this module* for their own type hints, which would
    otherwise be a circular import at module-load time (this module trying
    to import them, before `GraphState` even finishes being defined, while
    they simultaneously try to import `GraphState` back from this
    not-yet-fully-loaded module). Deferring these imports to call time --
    well after `GraphState` is fully defined -- is the standard, safe way to
    break that cycle without moving `GraphState` out of this file (which the
    file layout in this module's own docstring, and PROJECT_PLAN.md section
    10, both call for keeping here).
    """
    from app.agents.answer.node import make_answer_agent_node
    from app.agents.confidence import confidence_evaluation_node
    from app.agents.investigation.node import make_investigation_agent_node
    from app.agents.retrieval.node import make_retrieval_agent_node

    graph = StateGraph(GraphState)
    graph.add_node(
        "retrieval_agent",
        traced_node(
            "retrieval_agent",
            make_retrieval_agent_node(session, get_llm("rewrite")),
            attributes_fn=lambda _state, update: {
                "retrieval.chunk_count": len(update.get("retrieved_chunks") or []),
                "retrieval.rewritten": bool(update.get("rewritten_query")),
            },
        ),
    )
    graph.add_node(
        "confidence_evaluation",
        traced_node(
            "confidence_evaluation",
            confidence_evaluation_node,
            attributes_fn=lambda _state, update: {
                key: value
                for key, value in {
                    "confidence.score": update.get("confidence_score"),
                    "confidence.route": update.get("route"),
                }.items()
                if value is not None
            },
        ),
    )
    graph.add_node(
        "answer_agent",
        traced_node(
            "answer_agent",
            make_answer_agent_node(get_llm("generation"), get_llm("grounding_check")),
            attributes_fn=_answer_agent_span_attributes,
        ),
    )
    graph.add_node(
        "investigation_agent",
        traced_node(
            "investigation_agent",
            make_investigation_agent_node(session, get_llm("hypothesis")),
            attributes_fn=_investigation_agent_span_attributes,
        ),
    )

    graph.set_entry_point("retrieval_agent")
    graph.add_edge("retrieval_agent", "confidence_evaluation")
    graph.add_conditional_edges(
        "confidence_evaluation",
        _route_after_confidence,
        {"answer": "answer_agent", "investigation": "investigation_agent"},
    )
    graph.add_edge("answer_agent", END)
    graph.add_edge("investigation_agent", END)

    return graph.compile()


def build_investigation_graph(session: AsyncSession) -> Any:
    """Compile a second, separate graph containing only the Investigation
    Agent node -- see this module's docstring for why `triage_incident`
    needs its own graph rather than re-entering `build_graph`'s conditional
    edge.

    The Investigation Agent node import is local for the same circular-
    import reason `build_graph` defers its own node imports -- see that
    function's docstring.
    """
    from app.agents.investigation.node import make_investigation_agent_node

    graph = StateGraph(GraphState)
    graph.add_node(
        "investigation_agent",
        traced_node(
            "investigation_agent",
            make_investigation_agent_node(session, get_llm("hypothesis")),
            attributes_fn=_investigation_agent_span_attributes,
        ),
    )
    graph.set_entry_point("investigation_agent")
    graph.add_edge("investigation_agent", END)

    return graph.compile()
