"""Unit tests for `app.evaluation.judge`. Same "fake the true I/O edge (the
LLM), run the real logic" convention as `tests/agents/answer/
test_grounding.py`/`test_generation.py`'s `_FakeLLM`.
"""

from __future__ import annotations

import pytest

from app.evaluation.judge import judge_answer


class _FakeChatResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls = 0
        self.last_prompt: str | None = None

    async def ainvoke(self, prompt: str) -> _FakeChatResponse:
        self.calls += 1
        self.last_prompt = prompt
        return _FakeChatResponse(self.response_text)


def _judge_json(
    *,
    relevance: int = 8,
    grounded: str = "YES",
    hallucination: str = "NO",
    citation_accuracy: int = 7,
    completeness: int = 8,
    reasoning: str = "Well supported by the context.",
) -> str:
    return (
        "{"
        f'"relevance": {relevance}, "grounded": "{grounded}", '
        f'"hallucination": "{hallucination}", "citation_accuracy": {citation_accuracy}, '
        f'"completeness": {completeness}, "reasoning": "{reasoning}"'
        "}"
    )


@pytest.mark.asyncio
async def test_judge_answer_parses_a_well_formed_response() -> None:
    llm = _FakeLLM(_judge_json())

    result = await judge_answer(
        llm,
        question="How do we handle checkout 500s?",
        answer="Restart the checkout pod [1].",
        context_chunks=["Runbook: restart the checkout pod when it 500s."],
        citations=["https://runbooks.example/checkout"],
    )

    assert result.relevance_score == 8
    assert result.citation_accuracy_score == 7
    assert result.completeness_score == 8
    assert result.grounded is True
    assert result.hallucination_flag is False
    assert result.reasoning == "Well supported by the context."
    assert result.parse_error is None
    assert result.passed is True


@pytest.mark.asyncio
async def test_judge_answer_tolerates_a_markdown_fenced_response() -> None:
    """The prompt asks for "ONLY a single JSON object, no other text" but a
    judge model may still wrap it in a code fence -- `_extract_json`'s
    first-`{`-to-last-`}` extraction must still parse it.
    """
    llm = _FakeLLM("```json\n" + _judge_json() + "\n```")

    result = await judge_answer(
        llm, question="q", answer="a", context_chunks=["ctx"], citations=[]
    )

    assert result.parse_error is None
    assert result.relevance_score == 8


@pytest.mark.asyncio
async def test_judge_answer_fails_closed_on_non_json_response() -> None:
    """Regression: a judge response that isn't JSON at all must not raise --
    it degrades to a worst-case-scored, reportable `JudgeResult` (matching
    `tests/ingestion_retrieval/evaluate_answers.py`'s documented behavior).
    """
    llm = _FakeLLM("I cannot evaluate this answer.")

    result = await judge_answer(
        llm, question="q", answer="a", context_chunks=["ctx"], citations=[]
    )

    assert result.parse_error is not None
    assert result.relevance_score == 0
    assert result.citation_accuracy_score == 0
    assert result.completeness_score == 0
    assert result.grounded is False
    assert result.hallucination_flag is True
    assert result.passed is False


@pytest.mark.asyncio
async def test_judge_answer_fails_closed_when_llm_raises() -> None:
    class _RaisingLLM:
        async def ainvoke(self, prompt: str) -> None:
            raise RuntimeError("rate limited")

    result = await judge_answer(
        _RaisingLLM(), question="q", answer="a", context_chunks=["ctx"], citations=[]  # type: ignore[arg-type]
    )

    assert result.parse_error is not None
    assert "rate limited" in result.parse_error
    assert result.passed is False


@pytest.mark.asyncio
async def test_judge_answer_not_grounded_fails_even_with_high_scores() -> None:
    llm = _FakeLLM(_judge_json(grounded="NO", relevance=9, citation_accuracy=9))

    result = await judge_answer(
        llm, question="q", answer="a", context_chunks=["ctx"], citations=[]
    )

    assert result.grounded is False
    assert result.passed is False


@pytest.mark.asyncio
async def test_judge_answer_hallucination_fails_even_with_high_scores() -> None:
    llm = _FakeLLM(_judge_json(hallucination="YES", relevance=9, citation_accuracy=9))

    result = await judge_answer(
        llm, question="q", answer="a", context_chunks=["ctx"], citations=[]
    )

    assert result.hallucination_flag is True
    assert result.passed is False


@pytest.mark.asyncio
async def test_judge_answer_below_threshold_scores_fail() -> None:
    llm = _FakeLLM(_judge_json(relevance=5, citation_accuracy=9))
    result = await judge_answer(
        llm, question="q", answer="a", context_chunks=["ctx"], citations=[]
    )
    assert result.passed is False


@pytest.mark.asyncio
async def test_judge_answer_uses_no_context_placeholder_when_empty(monkeypatch) -> None:
    llm = _FakeLLM(_judge_json())

    await judge_answer(llm, question="q", answer="a", context_chunks=[], citations=[])

    assert llm.last_prompt is not None
    assert "(no context retrieved)" in llm.last_prompt


@pytest.mark.asyncio
async def test_judge_answer_coerces_string_scores() -> None:
    """A judge occasionally returns `"7"` instead of `7` -- this is a minor
    formatting slip, not a parse failure, and should still coerce cleanly.
    """
    raw = (
        '{"relevance": "8", "grounded": "YES", "hallucination": "NO", '
        '"citation_accuracy": "7", "completeness": "6", "reasoning": "ok"}'
    )
    llm = _FakeLLM(raw)

    result = await judge_answer(
        llm, question="q", answer="a", context_chunks=["ctx"], citations=[]
    )

    assert result.relevance_score == 8
    assert result.citation_accuracy_score == 7
    assert result.parse_error is None
