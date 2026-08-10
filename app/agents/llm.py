"""The one place agents/ constructs an LLM client, per ENGINEERING_DECISIONS.md
#008 (OpenAI, via `langchain-openai`).

**Model routing (Advanced Features Roadmap Phase 1, "Model routing (2.4)")**:
each LLM-calling task in this codebase is tagged with a coarse "cheap" or
"capable" tier, resolved to a real OpenAI model name via two independently
configurable settings (`agent_llm_model_cheap`/`agent_llm_model_capable`),
rather than every call site sharing the one `agent_llm_model` value this
module used before this feature. Both tiers default to the exact same model
value out of the box (`gpt-4o-mini`) -- this feature's job is to make
per-task routing *possible* and *observed* (see `log_llm_usage` below), not
to silently change which model any task uses; an operator only changes
behavior by setting the two settings to different values.

Task -> tier assignment (deliberately deviating from the Advanced Features
Roadmap's own illustrative `["rewrite", "confidence", "hypothesis",
"postmortem"]` literal list -- flagged here, not silently substituted):
"confidence" is dropped because it has no real LLM call to route
(`agents.confidence.confidence_evaluation_node` is pure arithmetic over
retrieval/grounding signals; it never calls an LLM). In its place, every
*actual* LLM call site in this codebase gets its own named task, including
the Answer Agent's two genuinely separate calls (generation vs. the
grounding-check escalation), which previously shared one label implicitly by
sharing one `llm` instance:

  - "rewrite"          -> cheap   (agents.retrieval.rewriting)
  - "generation"       -> capable (agents.answer.generation)
  - "grounding_check"  -> cheap   (agents.answer.grounding's LLM escalation)
  - "hypothesis"       -> capable (agents.investigation.hypothesis)
  - "postmortem"       -> capable (agents.postmortem.root_cause/action_items)
  - "knowledge_gap"    -> cheap   (agents.knowledge_gap.pipeline's topic
                           synthesis)
  - "judge"            -> cheap, temperature=0.0 (app.evaluation.judge's
                           LLM-as-judge call -- previously
                           `get_llm(temperature=0.0)` with no task identity)

Every task keeps this module's pre-existing default temperature (0.2)
except "judge", which keeps its own pre-existing `temperature=0.0` -- this
feature routes *models*, not temperatures; no task's generation behavior
changes as a side effect of this refactor alone.

`get_llm(task)` is now called from each real LLM call site (`agents/graph.py`
when building each node, `agents.service` for the two non-graph pipelines,
`evaluation.judge`/`evaluation.runner`), not once, centrally, in
`agents.service`, the way this module worked before Phase 1's Level 3: model
routing only takes effect if different tasks can actually end up on
different `ChatOpenAI` instances within the same request, which one shared
instance threaded through a whole graph run could never provide. See
`agents/graph.py`'s own module docstring for exactly which node gets which
task's client.

**Per-call usage tracking**: `log_llm_usage` records each real LLM call's
resolved model + token counts (from `AIMessage.usage_metadata`, when the
installed `langchain-openai` version and the provider's response populate
it) onto a `contextvars.ContextVar`-backed accumulator, scoped to one graph
run / pipeline call via `start_usage_tracking()`/`get_tracked_usage()` --
the same per-async-task context-propagation mechanism
`structlog.contextvars.merge_contextvars` already relies on elsewhere in
this codebase's logging setup, chosen here for an identical reason: every
real LLM call site is already several calls deep inside a call chain
(`rewriting.py`, `grounding.py`, `hypothesis.py`, ...) and needs to
contribute usage data up to `agents.service`'s per-run `agent_executions`
row without a large `GraphState`/return-value plumbing change threaded
through every intermediate function signature.
"""

from __future__ import annotations

from contextvars import ContextVar
from functools import lru_cache
from typing import Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.shared.config.logging import get_logger
from app.shared.config.settings import get_settings

logger = get_logger(__name__)

AgentTask = Literal[
    "rewrite",
    "generation",
    "grounding_check",
    "hypothesis",
    "postmortem",
    "knowledge_gap",
    "judge",
]

_ModelTier = Literal["cheap", "capable"]

_TASK_TIERS: dict[AgentTask, _ModelTier] = {
    "rewrite": "cheap",
    "generation": "capable",
    "grounding_check": "cheap",
    "hypothesis": "capable",
    "postmortem": "capable",
    "knowledge_gap": "cheap",
    "judge": "cheap",
}

# Every task keeps this module's pre-existing default temperature (0.2)
# except "judge", which keeps `get_llm(temperature=0.0)`'s prior judging-
# determinism value -- see module docstring: this feature routes models,
# not temperatures.
_TASK_TEMPERATURES: dict[AgentTask, float] = {
    "rewrite": 0.2,
    "generation": 0.2,
    "grounding_check": 0.2,
    "hypothesis": 0.2,
    "postmortem": 0.2,
    "knowledge_gap": 0.2,
    "judge": 0.0,
}


def model_for_task(task: AgentTask) -> str:
    """Resolve `task` to a real OpenAI model name, without constructing a
    client -- used both by `get_llm` below and by callers (`agents.service`,
    `evaluation.runner`) that need to record which model a task uses for
    observability (e.g. an `agent_executions.model_used` value), independent
    of actually calling it.
    """
    settings = get_settings()
    tier = _TASK_TIERS[task]
    return settings.agent_llm_model_capable if tier == "capable" else settings.agent_llm_model_cheap


@lru_cache
def _build_client(model: str, temperature: float) -> ChatOpenAI:
    """Cached by the resolved `(model, temperature)` pair, not by `task` --
    two tasks that happen to resolve to the same model/temperature share one
    `ChatOpenAI` client rather than each constructing its own redundant
    instance, the same reasoning this module's pre-existing `@lru_cache`
    already applied to its single-argument predecessor.
    """
    settings = get_settings()
    return ChatOpenAI(model=model, api_key=settings.openai_api_key, temperature=temperature)


def get_llm(task: AgentTask, *, temperature: float | None = None) -> ChatOpenAI:
    """Return the `ChatOpenAI` client for `task`, per this module's model-
    routing table (see module docstring).

    `temperature`, if given, overrides `task`'s own default -- the same
    override every caller could already do before this feature existed
    (`evaluation.judge`'s prior `get_llm(temperature=0.0)` call is now just
    `get_llm("judge")`, since 0.0 is already that task's own default).
    """
    resolved_temperature = temperature if temperature is not None else _TASK_TEMPERATURES[task]
    model = model_for_task(task)
    return _build_client(model, resolved_temperature)


class LlmUsageRecord(BaseModel):
    """One real LLM call's resolved model + token usage -- see
    `log_llm_usage`.
    """

    task: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


_usage_records: ContextVar[list[LlmUsageRecord] | None] = ContextVar(
    "ekip_agents_llm_usage_records", default=None
)


def start_usage_tracking() -> None:
    """Begin a new usage-tracking scope for the current async task -- call
    once at the start of one graph run / pipeline call (`agents.service`),
    before any node/step that might call `log_llm_usage`. Calling this again
    within the same context simply discards whatever was collected so far,
    which is exactly "start a fresh scope" -- there is no nesting concept
    here, matching how one `agents.service` entry point corresponds to one
    `agent_executions` row.
    """
    _usage_records.set([])


def get_tracked_usage() -> list[LlmUsageRecord]:
    """Return every `LlmUsageRecord` logged since the most recent
    `start_usage_tracking()` call in this context (contextvars propagate
    across `await`, matching `structlog.contextvars`'s equivalent
    per-request isolation) -- empty if `start_usage_tracking()` was never
    called in this context, e.g. an ad-hoc script call that never opted in.
    """
    return list(_usage_records.get() or [])


def log_llm_usage(task: AgentTask, llm: BaseChatModel, response: BaseMessage) -> None:
    """Record one real LLM call's resolved model + token usage (from
    `response.usage_metadata`, populated by `langchain-openai` when the
    provider returns usage data) -- called from each real `llm.ainvoke(...)`
    call site (`rewriting.py`, `generation.py`, `grounding.py`,
    `hypothesis.py`, `root_cause.py`, `action_items.py`,
    `knowledge_gap.pipeline`, `evaluation.judge`), immediately after the call
    succeeds, never before -- a failed call has no usage to record.

    `llm` is typed as the generic `BaseChatModel` (not `ChatOpenAI`)
    deliberately: every real call site above already types its own `llm`
    parameter as `BaseChatModel` (matching this codebase's existing
    convention of depending on the LangChain interface, not a concrete
    provider, at every call site below `agents.llm` itself) -- `model_name`
    is read defensively via `getattr` precisely because it is not part of
    that generic interface.

    Always logs a structured event regardless of whether a tracking scope is
    active (useful even outside a tracked run, e.g. an ad-hoc script call);
    only *appends* to the current scope's accumulator when one is active
    (see `start_usage_tracking`).
    """
    usage = getattr(response, "usage_metadata", None) or {}
    model = getattr(llm, "model_name", None) or getattr(llm, "model", None) or model_for_task(task)
    record = LlmUsageRecord(
        task=task,
        model=model,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        total_tokens=usage.get("total_tokens"),
    )
    logger.info(
        "llm_call_completed",
        task=task,
        model=record.model,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        total_tokens=record.total_tokens,
    )
    records = _usage_records.get()
    if records is not None:
        records.append(record)
