"""Evaluation harness orchestration -- `run_evaluation`, the "script"
Phase 1's roadmap entry describes: loop the golden set through the real,
unmodified `app.agents.service.answer_question`, score each answer with
`judge.judge_answer`, and persist a trend-storage row per run/case.

Deliberately mirrors `agents.service.detect_knowledge_gaps`'s own shape (a
scheduled/script-driven agent-adjacent run, `Identity.for_agent(...)`, one
run-level `status="running"` row created up front, updated to
`succeeded`/`failed` at the end) rather than `answer_question`'s two-tier
failure handling: like `detect_knowledge_gaps`, this function's own overall
run either fully succeeds or is marked `failed` and re-raised -- there is no
"fabricated degraded eval run" the way `AskResponse` has a generic-failure
shape, since a harness run silently reporting fake numbers would defeat its
entire purpose.

Per-*case* failure handling is the opposite, and deliberate (per
`app.database.models.evaluation_models.EvalCaseResult`'s own docstring: "a
case can fail before the judge ever runs ... the row still gets written --
`passed=False`, every score `NULL`"): `_run_one_case` catches every
exception itself and always returns a (possibly all-null) `EvalCaseOutcome`,
so one bad golden question never aborts the rest of the run. The outer
`run_evaluation` try/except around the loop is a backstop for genuine
infrastructure failures (e.g. `insert_eval_case_result` itself failing), not
a place case-scoring failures are expected to surface.

`route_taken == "investigation"` responses (`AskResponse.answer is None`) are
recorded but not scored by this rubric -- `judge.judge_answer`'s five-axis
rubric is answer-shaped (does the *answer* address the question), and
scoring a `RootCauseHypothesis` the same way would silently misapply it,
not gracefully degrade it. Extending this harness to grade the Investigation
Agent's hypotheses is a real gap, flagged here rather than papered over with
a wrong number -- see AGENT_WORKFLOWS.md section 2.7's reflection-loop work
(Phase 2/3) as the more natural place a hypothesis-specific rubric belongs.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from langchain_core.language_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm import get_llm, model_for_task
from app.agents.service import answer_question
from app.database.models.ingestion_models import Document
from app.evaluation import repository
from app.evaluation.judge import judge_answer
from app.evaluation.schemas import EvalCaseOutcome, EvalRunSummary, GoldenCase, JudgeResult
from app.shared.config.logging import get_logger
from app.shared.schemas import AskResponse, Citation, Identity

logger = get_logger(__name__)

_INVESTIGATION_ROUTE_REASONING = (
    "Routed to investigation, not scored by the answer-quality rubric "
    "(see app.evaluation.runner's module docstring)."
)


async def run_evaluation(
    session: AsyncSession,
    actor: Identity,
    golden_cases: list[GoldenCase],
    *,
    git_commit: str | None = None,
) -> EvalRunSummary:
    """Run every case in `golden_cases` through `answer_question` (as
    `actor`, within `actor.organization_id`), score each with an LLM-judge,
    and persist one `eval_runs` row plus one `eval_case_results` row per
    case.

    `actor` should be an `Identity.for_agent("evaluation_harness", ...)`-
    style identity, the same convention `detect_knowledge_gaps` establishes
    for scheduled/script-driven callers -- `scripts/run_evaluation.py`
    constructs it, and is also responsible for calling
    `app.database.session.set_tenant_context` on `session` first (this
    function does not call it itself, matching every other service-layer
    function in this codebase: that call is always the caller's
    responsibility, never the callee's).

    Raises whatever escapes case-loop bookkeeping itself (never a scoring
    failure -- see module docstring) after marking the `eval_runs` row
    `failed`.
    """
    # "generation" is the task whose output this rubric actually grades (the
    # Answer Agent's drafted answer) -- recorded as the run's `model_used`
    # even though `answer_question` itself may route several other tasks
    # (rewrite/grounding_check/...) to different models internally; see
    # `app.agents.llm`'s module docstring for the full task -> tier table.
    execution = await repository.insert_eval_run(
        session,
        organization_id=actor.organization_id,
        model_used=model_for_task("generation"),
        git_commit=git_commit,
    )

    outcomes: list[EvalCaseOutcome] = []
    try:
        judge_llm = get_llm("judge")
        for case in golden_cases:
            outcome = await _run_one_case(session, actor, case, judge_llm)
            await repository.insert_eval_case_result(
                session,
                organization_id=actor.organization_id,
                eval_run_id=execution.id,
                case_id=outcome.case_id,
                question=outcome.question,
                route_taken=outcome.route_taken,
                confidence_score=outcome.confidence_score,
                citation_count=outcome.citation_count,
                expected_sources=outcome.expected_sources,
                actual_sources=outcome.actual_sources,
                relevance_score=outcome.relevance_score,
                citation_accuracy_score=outcome.citation_accuracy_score,
                completeness_score=outcome.completeness_score,
                grounded=outcome.grounded,
                hallucination_flag=outcome.hallucination_flag,
                passed=outcome.passed,
                judge_reasoning=outcome.judge_reasoning,
                error_detail=outcome.error_detail,
            )
            outcomes.append(outcome)
    except Exception as exc:
        logger.error(
            "evaluation_run_unexpected_failure", eval_run_id=str(execution.id), error=str(exc)
        )
        await repository.update_eval_run(
            session,
            execution.id,
            status="failed",
            error_detail=str(exc)[:2000],
            completed_at=datetime.now(timezone.utc),
        )
        raise

    passed_count = sum(1 for outcome in outcomes if outcome.passed)
    hallucination_count = sum(1 for outcome in outcomes if outcome.hallucination_flag)
    avg_relevance_score = _average(outcome.relevance_score for outcome in outcomes)
    avg_citation_accuracy_score = _average(
        outcome.citation_accuracy_score for outcome in outcomes
    )
    avg_confidence_score = _average(outcome.confidence_score for outcome in outcomes)

    await repository.update_eval_run(
        session,
        execution.id,
        status="succeeded",
        case_count=len(outcomes),
        passed_count=passed_count,
        hallucination_count=hallucination_count,
        avg_relevance_score=avg_relevance_score,
        avg_citation_accuracy_score=avg_citation_accuracy_score,
        avg_confidence_score=avg_confidence_score,
        completed_at=datetime.now(timezone.utc),
    )

    return EvalRunSummary(
        eval_run_id=execution.id,
        organization_id=actor.organization_id,
        model_used=model_for_task("generation"),
        git_commit=git_commit,
        case_count=len(outcomes),
        passed_count=passed_count,
        hallucination_count=hallucination_count,
        avg_relevance_score=avg_relevance_score,
        avg_citation_accuracy_score=avg_citation_accuracy_score,
        avg_confidence_score=avg_confidence_score,
        status="succeeded",
        cases=outcomes,
    )


async def _run_one_case(
    session: AsyncSession, actor: Identity, case: GoldenCase, judge_llm: BaseChatModel
) -> EvalCaseOutcome:
    """Run one golden case end-to-end, never raising (see module
    docstring): every failure mode -- `answer_question` itself raising, the
    investigation-route case, source-resolution/judging failing -- resolves
    to a concrete `EvalCaseOutcome`, not an exception.
    """
    try:
        response = await answer_question(
            session, case.question, None, actor, trigger_source="scheduled"
        )
    except Exception as exc:
        logger.warning(
            "evaluation_case_answer_question_failed",
            case_id=case.case_id,
            question=case.question,
            error=str(exc),
        )
        return EvalCaseOutcome(
            case_id=case.case_id,
            question=case.question,
            expected_sources=case.expected_sources,
            error_detail=str(exc)[:2000],
        )

    if response.answer is None:
        return EvalCaseOutcome(
            case_id=case.case_id,
            question=case.question,
            route_taken=response.route_taken,
            confidence_score=response.confidence,
            citation_count=len(response.citations),
            expected_sources=case.expected_sources,
            judge_reasoning=_INVESTIGATION_ROUTE_REASONING,
        )

    try:
        actual_sources = await _resolve_actual_sources(session, response.citations)
        judge_result = await judge_answer(
            judge_llm,
            question=case.question,
            answer=response.answer,
            context_chunks=[citation.excerpt for citation in response.citations],
            citations=[
                citation.source_url or str(citation.document_id)
                for citation in response.citations
            ],
        )
    except Exception as exc:
        logger.warning(
            "evaluation_case_judging_failed",
            case_id=case.case_id,
            question=case.question,
            error=str(exc),
        )
        return EvalCaseOutcome(
            case_id=case.case_id,
            question=case.question,
            route_taken=response.route_taken,
            confidence_score=response.confidence,
            citation_count=len(response.citations),
            expected_sources=case.expected_sources,
            error_detail=str(exc)[:2000],
        )

    return _outcome_from_judge_result(case, response, actual_sources, judge_result)


def _outcome_from_judge_result(
    case: GoldenCase,
    response: AskResponse,
    actual_sources: list[str],
    judge_result: JudgeResult,
) -> EvalCaseOutcome:
    return EvalCaseOutcome(
        case_id=case.case_id,
        question=case.question,
        route_taken=response.route_taken,
        confidence_score=response.confidence,
        citation_count=len(response.citations),
        expected_sources=case.expected_sources,
        actual_sources=actual_sources,
        relevance_score=judge_result.relevance_score,
        citation_accuracy_score=judge_result.citation_accuracy_score,
        completeness_score=judge_result.completeness_score,
        grounded=judge_result.grounded,
        hallucination_flag=judge_result.hallucination_flag,
        passed=judge_result.passed,
        judge_reasoning=judge_result.reasoning,
        error_detail=judge_result.parse_error,
    )


async def _resolve_actual_sources(
    session: AsyncSession, citations: list[Citation]
) -> list[str]:
    """Resolve each citation's `document_id` back to its `Document.source`
    (`"slack"`, `"github"`, ...) -- `Citation` itself carries no source
    label (see `app.shared.schemas.agent_contracts.Citation`), so this does
    the same read-only join `tests/ingestion_retrieval/
    test_end_to_end_rag.py::_resolve_citation_sources` already established
    for this exact purpose, deduplicated and sorted for a deterministic,
    JSON-serializable list.
    """
    if not citations:
        return []
    document_ids = {citation.document_id for citation in citations}
    stmt = select(Document.source).where(Document.id.in_(document_ids)).distinct()
    result = await session.execute(stmt)
    return sorted(result.scalars().all())


def _average(values: Iterable[int | float | None]) -> float | None:
    """Mean of the non-`None` entries in `values`, or `None` if every entry
    is `None` -- e.g. every case failed before scoring, so there is
    genuinely nothing to average, not a zero to report.
    """
    numeric_values = [value for value in values if value is not None]
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)
