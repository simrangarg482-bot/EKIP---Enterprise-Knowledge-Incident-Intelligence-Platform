"""Unit tests for `app.agents.llm` -- task-tiered model routing + per-call
usage tracking (Advanced Features Roadmap Phase 1, "Model routing (2.4)").

`ChatOpenAI` itself is never constructed for real here (no network call, no
real API key needed): `_build_client`'s only real-world dependency is a
class instantiable with `model=`/`api_key=`/`temperature=` kwargs, so it is
swapped for a lightweight recording fake -- the same "replace the one real
external constructor, exercise the real routing logic around it" approach
`tests/shared/config/test_tracing.py` uses for `trace.set_tracer_provider`.

Both of this module's process-global caches (`_build_client`'s `lru_cache`
and the `_usage_records` contextvar) are reset before/after every test here
-- without that, a contextvar `.set()` in one test would leak into whichever
test runs next in the same thread (contextvars are thread-scoped, not
per-test), and a stale cached client from a different test's fake settings
would silently defeat the "resolves per-tier model" assertions below.
"""

from __future__ import annotations

import pytest

from app.agents import llm as llm_module


class _FakeSettings:
    def __init__(
        self,
        *,
        agent_llm_model_cheap: str = "cheap-model",
        agent_llm_model_capable: str = "capable-model",
        openai_api_key: str = "sk-test",
    ) -> None:
        self.agent_llm_model_cheap = agent_llm_model_cheap
        self.agent_llm_model_capable = agent_llm_model_capable
        self.openai_api_key = openai_api_key


class _FakeChatOpenAI:
    """Stands in for `langchain_openai.ChatOpenAI` -- records constructor
    args instead of building a real OpenAI HTTP client.
    """

    def __init__(self, *, model: str, api_key: str, temperature: float) -> None:
        self.model_name = model
        self.api_key = api_key
        self.temperature = temperature


class _FakeUsageResponse:
    def __init__(self, usage_metadata: dict[str, int] | None) -> None:
        self.usage_metadata = usage_metadata


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch: pytest.MonkeyPatch):
    llm_module._build_client.cache_clear()
    monkeypatch.setattr(llm_module, "ChatOpenAI", _FakeChatOpenAI)
    monkeypatch.setattr(llm_module, "get_settings", lambda: _FakeSettings())
    llm_module._usage_records.set(None)
    yield
    llm_module._build_client.cache_clear()
    llm_module._usage_records.set(None)


@pytest.mark.parametrize(
    "task,expected_model",
    [
        ("rewrite", "cheap-model"),
        ("grounding_check", "cheap-model"),
        ("knowledge_gap", "cheap-model"),
        ("judge", "cheap-model"),
        ("generation", "capable-model"),
        ("hypothesis", "capable-model"),
        ("postmortem", "capable-model"),
    ],
)
def test_model_for_task_resolves_expected_tier(task: str, expected_model: str) -> None:
    assert llm_module.model_for_task(task) == expected_model  # type: ignore[arg-type]


def test_get_llm_resolves_model_and_default_temperature() -> None:
    client = llm_module.get_llm("generation")
    assert client.model_name == "capable-model"
    assert client.temperature == 0.2


def test_get_llm_judge_task_defaults_to_zero_temperature() -> None:
    """Preserves `evaluation.judge`'s prior, pre-routing
    `get_llm(temperature=0.0)` judging-determinism behavior exactly.
    """
    client = llm_module.get_llm("judge")
    assert client.model_name == "cheap-model"
    assert client.temperature == 0.0


def test_get_llm_temperature_override_wins_over_task_default() -> None:
    client = llm_module.get_llm("rewrite", temperature=0.9)
    assert client.temperature == 0.9


def test_get_llm_caches_by_resolved_model_and_temperature_not_by_task() -> None:
    """Two different tasks that happen to resolve to the same
    `(model, temperature)` pair share one client -- see `_build_client`'s
    own docstring on why caching is keyed there, not by `task`.
    """
    # "grounding_check" and "knowledge_gap" both default to the cheap tier
    # at temperature 0.2 -- the same resolved pair.
    a = llm_module.get_llm("grounding_check")
    b = llm_module.get_llm("knowledge_gap")
    assert a is b


def test_get_llm_returns_distinct_clients_for_distinct_tiers() -> None:
    cheap = llm_module.get_llm("rewrite")
    capable = llm_module.get_llm("generation")
    assert cheap is not capable
    assert cheap.model_name != capable.model_name


def test_log_llm_usage_is_a_noop_outside_a_tracking_scope() -> None:
    """No `start_usage_tracking()` call in this test -- `log_llm_usage` must
    not raise, and nothing gets recorded.
    """
    llm = llm_module.get_llm("rewrite")
    response = _FakeUsageResponse({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})

    llm_module.log_llm_usage("rewrite", llm, response)

    assert llm_module.get_tracked_usage() == []


def test_start_usage_tracking_then_log_llm_usage_records_a_call() -> None:
    llm_module.start_usage_tracking()
    llm = llm_module.get_llm("generation")
    response = _FakeUsageResponse({"input_tokens": 100, "output_tokens": 40, "total_tokens": 140})

    llm_module.log_llm_usage("generation", llm, response)

    records = llm_module.get_tracked_usage()
    assert len(records) == 1
    assert records[0].task == "generation"
    assert records[0].model == "capable-model"
    assert records[0].input_tokens == 100
    assert records[0].output_tokens == 40
    assert records[0].total_tokens == 140


def test_log_llm_usage_accumulates_multiple_calls_in_one_scope() -> None:
    llm_module.start_usage_tracking()
    rewrite_llm = llm_module.get_llm("rewrite")
    generation_llm = llm_module.get_llm("generation")

    llm_module.log_llm_usage(
        "rewrite",
        rewrite_llm,
        _FakeUsageResponse({"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}),
    )
    llm_module.log_llm_usage(
        "generation",
        generation_llm,
        _FakeUsageResponse({"input_tokens": 50, "output_tokens": 20, "total_tokens": 70}),
    )

    records = llm_module.get_tracked_usage()
    assert [record.task for record in records] == ["rewrite", "generation"]
    assert sum(record.total_tokens for record in records) == 77


def test_start_usage_tracking_resets_the_scope() -> None:
    llm_module.start_usage_tracking()
    llm = llm_module.get_llm("rewrite")
    llm_module.log_llm_usage(
        "rewrite", llm, _FakeUsageResponse({"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    )
    assert len(llm_module.get_tracked_usage()) == 1

    llm_module.start_usage_tracking()  # a fresh scope discards prior records
    assert llm_module.get_tracked_usage() == []


def test_log_llm_usage_handles_missing_usage_metadata_gracefully() -> None:
    """Not every provider response populates `usage_metadata` -- this must
    degrade to `None` fields, never raise.
    """
    llm_module.start_usage_tracking()
    llm = llm_module.get_llm("rewrite")

    class _NoUsageResponse:
        pass

    llm_module.log_llm_usage("rewrite", llm, _NoUsageResponse())  # type: ignore[arg-type]

    records = llm_module.get_tracked_usage()
    assert records[0].input_tokens is None
    assert records[0].output_tokens is None
    assert records[0].total_tokens is None
