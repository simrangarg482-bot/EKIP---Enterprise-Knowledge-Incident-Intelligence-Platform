"""LLM-as-judge scoring for the evaluation harness -- Phase 1's "LLM-judge
scoring rubric (groundedness, relevance, hallucination rate)".

This is a production-code generalization of `tests/ingestion_retrieval/
evaluate_answers.py::evaluate_answer` (same five-axis rubric: relevance,
grounded, hallucination, citation_accuracy, completeness; same JSON-object
prompt contract; same "judge failure is a reportable result, not a crash"
behavior) -- moved into `app/` so `app.evaluation.runner` can import it
without depending on `tests/`, and returning a typed `JudgeResult`
(`schemas.py`) instead of a raw dict.

Deliberately a *separate* LLM-as-judge from EKIP's own production grounding
check (`app.agents.answer.grounding.verify_grounding`, which runs
automatically inside `answer_question` and already strips ungrounded
sentences before an answer is ever returned): that check tells you "does
EKIP's own pipeline believe its answer is grounded," which cannot also serve
as this harness's independent, external quality check -- it would just be
grading EKIP's homework with EKIP's own answer key. This module calls the
real, unmodified `app.agents.llm.get_llm("judge")` (same OpenAI client the
app itself uses, at that task's own `temperature=0.0` default for judging
determinism) with its own, distinct evaluation prompt.

**Model routing (Advanced Features Roadmap Phase 1, "Model routing (2.4)")**:
`app.evaluation.runner` now calls `get_llm("judge")` (a dedicated task, cheap
tier, temperature 0.0) rather than the earlier no-`task` `get_llm(
temperature=0.0)` this module's docstring previously described as deferred
to "Phase 1's next, separate roadmap item" -- that item is this one. See
`app.agents.llm`'s module docstring for the full task -> tier table.
"""

from __future__ import annotations

import json

from langchain_core.language_models import BaseChatModel

from app.agents.llm import log_llm_usage
from app.evaluation.schemas import JudgeResult
from app.shared.config.logging import get_logger

logger = get_logger(__name__)

_JUDGE_PROMPT_TEMPLATE = """You are an impartial evaluator grading a RAG system's answer. Judge STRICTLY \
based on the retrieved context provided below -- do not use outside knowledge of your own.

Question:
{question}

Retrieved Context (what the system had available to answer from):
{context}

Generated Answer:
{answer}

Citations Provided: {citations}

Score the answer on these five axes and respond with ONLY a single JSON object, no other text, using \
exactly these keys:
{{
  "relevance": <integer 1-10, does the answer address the question>,
  "grounded": "YES" or "NO" -- is the answer supported by the retrieved context above,
  "hallucination": "YES" or "NO" -- does the answer contain claims NOT present in the retrieved context,
  "citation_accuracy": <integer 1-10, do the citations actually support the claims they're attached to>,
  "completeness": <integer 1-10, did the answer address every part of the question>,
  "reasoning": "<one or two sentence justification>"
}}"""

# Same "judge returned no answer" convention `evaluate_answers.py` uses when
# `answer` is empty/None (e.g. `AskResponse.answer is None` on the
# `route_taken == "investigation"` path) -- a judge is never asked to grade
# a null answer against this rubric; `runner.py` special-cases that before
# ever calling this function (see that module's docstring).
_NO_CONTEXT_PLACEHOLDER = "(no context retrieved)"


async def judge_answer(
    llm: BaseChatModel,
    *,
    question: str,
    answer: str,
    context_chunks: list[str],
    citations: list[str],
) -> JudgeResult:
    """Score one generated `answer` against `context_chunks` (the actual
    retrieved chunk excerpts it was generated from, not just titles) and
    `citations` (the citation markers/urls attached to it).

    Never raises: a judge call/parse failure produces a `JudgeResult` with
    `parse_error` set and every score at its safe, worst-case default
    (relevance/citation_accuracy/completeness=0, grounded=False,
    hallucination_flag=True) -- matching `evaluate_answers.py`'s own
    documented behavior exactly, so a broken judge shows up as a visible,
    reportable failure in the trend data rather than a crash or a silent
    pass.
    """
    context_block = (
        "\n\n".join(f"[{index}] {chunk}" for index, chunk in enumerate(context_chunks, start=1))
        or _NO_CONTEXT_PLACEHOLDER
    )
    prompt = _JUDGE_PROMPT_TEMPLATE.format(
        question=question, context=context_block, answer=answer, citations=citations
    )

    try:
        response = await llm.ainvoke(prompt)
        log_llm_usage("judge", llm, response)
        raw_response = str(response.content)
        parsed = json.loads(_extract_json(raw_response))
    except Exception as exc:  # noqa: BLE001 - a judge failure is itself a result to report
        logger.warning("evaluation_judge_call_or_parse_failed", question=question, error=str(exc))
        return JudgeResult(
            relevance_score=0,
            citation_accuracy_score=0,
            completeness_score=0,
            grounded=False,
            hallucination_flag=True,
            reasoning=f"Judge call/parse failed: {exc}",
            parse_error=str(exc),
        )

    grounded = str(parsed.get("grounded", "NO")).upper() == "YES"
    hallucination = str(parsed.get("hallucination", "YES")).upper() == "YES"

    return JudgeResult(
        relevance_score=_coerce_score(parsed.get("relevance")),
        citation_accuracy_score=_coerce_score(parsed.get("citation_accuracy")),
        completeness_score=_coerce_score(parsed.get("completeness")),
        grounded=grounded,
        hallucination_flag=hallucination,
        reasoning=str(parsed.get("reasoning", "")),
    )


def _coerce_score(raw_value: object) -> int:
    """Best-effort int coercion for a judge-reported score -- a judge that
    returns `"7"` (string) or `7.0` (float) instead of `7` (int) is a minor
    formatting slip, not a reason to fail the whole case the way a
    non-JSON response is; falls back to `0` for anything genuinely
    uncoercible.
    """
    try:
        return int(raw_value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _extract_json(text: str) -> str:
    """Same tolerant extraction `evaluate_answers.py::_extract_json` uses:
    find the first `{` and last `}` rather than requiring the whole response
    to be nothing but JSON, since judge models occasionally add a stray
    leading/trailing word despite being told not to.
    """
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in judge response: {text!r}")
    return text[start : end + 1]
