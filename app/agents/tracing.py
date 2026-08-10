"""Per-LangGraph-node OTel span wrapping (Advanced Features Roadmap Phase 1,
"OTel tracing (2.3)"). `traced_node` wraps one graph node callable in its
own span, named `agent.<node_name>` -- so a full request's span tree shows
exactly which nodes ran, how long each took, and a handful of node-specific
attributes (confidence score, route taken, citation/hypothesis counts, ...).

Deliberately the *only* new file this feature adds inside `agents/` (plus
`agents/graph.py`'s own node-registration call sites, where `traced_node` is
actually applied) -- `agents/retrieval/node.py`, `confidence.py`,
`agents/answer/node.py`, and `agents/investigation/node.py` are all
untouched. The roadmap's own integration note for this feature says exactly
this: "wrap each node in agents/graph.py in a span" -- the wrapping belongs
at the point nodes are registered onto the `StateGraph`, not scattered
across each node's own implementation file.

Any exception a node raises is recorded on its span (`span.record_exception`
+ an ERROR status) and re-raised completely unchanged -- this module adds
observability only; it must never change a node's own error-handling
contract (`agents.retry.call_with_retry`'s retry semantics, `agents.
service`'s two-tier failure handling, etc. all still see the exact same
exception they would have without tracing).

No runtime import of `agents.graph.GraphState` here, despite every type hint
below referencing it: importing it at module level would be circular
(`agents/graph.py` needs to import `traced_node` from this module to wrap
its own nodes) -- the same cycle `agents.graph.build_graph`'s own docstring
describes for node-factory imports, broken here via `TYPE_CHECKING` +
`from __future__ import annotations` (annotations become strings, never
evaluated at runtime) rather than a deferred call-time import, since nothing
in this module needs a real `GraphState` value at runtime, only its name for
type checkers.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

if TYPE_CHECKING:
    from app.agents.graph import GraphState

_tracer = trace.get_tracer("app.agents")

_NodeUpdate = dict[str, Any]
_SyncNode = Callable[["GraphState"], _NodeUpdate]
_AsyncNode = Callable[["GraphState"], Awaitable[_NodeUpdate]]
# A `None` value is a legal entry -- `_apply_attributes` silently skips it
# (a node-specific attribute that genuinely has nothing to report, e.g. no
# `result` yet, is common enough that requiring every `attributes_fn` to
# filter its own `None`s would just move the same `if value is not None`
# check into every call site instead of this one shared place).
AttributesFn = Callable[["GraphState", _NodeUpdate], dict[str, str | int | float | bool | None]]


def traced_node(
    node_name: str,
    node: _SyncNode | _AsyncNode,
    *,
    attributes_fn: AttributesFn | None = None,
) -> _SyncNode | _AsyncNode:
    """Wrap `node` in a span named `agent.<node_name>`.

    Dispatches on `inspect.iscoroutinefunction(node)` once, at graph-build
    time (not per invocation) -- `confidence_evaluation_node` is the one
    synchronous node in this graph (see that module's own docstring on why);
    every other node this wraps is a factory-produced async closure.

    `attributes_fn(state, update)`, if given, is called only after `node`
    returns successfully (never after a raise) with the incoming `state` and
    the partial-state `update` dict `node` returned; its return value is set
    as extra span attributes. Node-specific extraction logic belongs in the
    lambda passed at each `graph.add_node(...)` call site in `agents/
    graph.py`, not in this generic wrapper.
    """
    if inspect.iscoroutinefunction(node):

        async def async_wrapper(state: GraphState) -> _NodeUpdate:
            with _tracer.start_as_current_span(f"agent.{node_name}") as span:
                span.set_attribute("agent.node_name", node_name)
                try:
                    update = await node(state)  # type: ignore[misc]
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise
                _apply_attributes(span, state, update, attributes_fn)
                return update

        return async_wrapper

    def sync_wrapper(state: GraphState) -> _NodeUpdate:
        with _tracer.start_as_current_span(f"agent.{node_name}") as span:
            span.set_attribute("agent.node_name", node_name)
            try:
                update = node(state)  # type: ignore[arg-type]
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                raise
            _apply_attributes(span, state, update, attributes_fn)
            return update

    return sync_wrapper


def _apply_attributes(
    span: Span,
    state: GraphState,
    update: _NodeUpdate,
    attributes_fn: AttributesFn | None,
) -> None:
    if attributes_fn is None:
        return
    for key, value in attributes_fn(state, update).items():
        if value is not None:
            span.set_attribute(key, value)
