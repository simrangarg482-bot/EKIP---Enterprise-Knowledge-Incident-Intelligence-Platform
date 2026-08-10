"""Pydantic contracts for evaluation/ -- the golden-set question format, the
LLM-judge's scoring result, and the run-level summary `runner.py` returns.

Owned by: evaluation/. Kept separate from `repository.py`'s ORM rows (same
split `app.agents.schemas.AgentExecutionStats` makes from
`app.agents.repository`'s raw `Row` objects) since none of these are
persisted verbatim -- `GoldenCase` is loaded from JSON, `JudgeResult` is an
intermediate scoring artifact folded into an `EvalCaseResult` row, and
`EvalRunSummary` is what a CLI/future dashboard reads back, not a table.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field


class GoldenCase(BaseModel):
    """One hand-authored golden-set question (Phase 1's "even 100-200
    hand-labeled Q/A pairs with expected citations" -- the bundled starter
    set in `golden_qa_set.json` is intentionally much smaller; see
    `golden_set.py`'s module docstring for why).

    `expected_sources` holds `Document.source` values (`"slack"`, `"github"`,
    `"runbooks"`, ...) the question is expected to be answerable from --
    matching `tests/ingestion_retrieval/test_end_to_end_rag.py`'s
    already-established `questions.json` schema (`question` +
    `expected_sources`), not a new format invented for this harness.
    """

    model_config = ConfigDict(frozen=True)

    case_id: str
    question: str
    expected_sources: list[str] = Field(default_factory=list)
    notes: str | None = None


class JudgeResult(BaseModel):
    """One LLM-judge scoring of a single generated answer -- the typed
    equivalent of `tests/ingestion_retrieval/evaluate_answers.py`'s raw
    dict return shape, adapted to production code (`app/`, not `tests/`) so
    `runner.py` can import it without a test-harness dependency.

    `parse_error` is set (and every score left at its safe default) when the
    judge's own response wasn't valid, expected-shape JSON -- a judge
    formatting failure is a real, reportable result, not something to raise
    on (same reasoning `evaluate_answers.py`'s own docstring gives).
    """

    model_config = ConfigDict(frozen=True)

    relevance_score: int
    citation_accuracy_score: int
    completeness_score: int
    grounded: bool
    hallucination_flag: bool
    reasoning: str
    parse_error: str | None = None

    @property
    def passed(self) -> bool:
        """Same PASS criteria `evaluate_answers.py::evaluate_answer` already
        established: grounded, no hallucination, and both integer scores
        clearing a "good enough" bar -- reused rather than re-derived so this
        harness's pass/fail line matches the one already proven out in this
        codebase's own manual RAG-quality testing.
        """
        return (
            self.grounded
            and not self.hallucination_flag
            and self.relevance_score >= _PASS_RELEVANCE_THRESHOLD
            and self.citation_accuracy_score >= _PASS_CITATION_ACCURACY_THRESHOLD
        )


# Same numeric bar `evaluate_answers.py` hardcodes (`relevance >= 6 and
# citation_accuracy >= 6`) -- module-level constants here rather than
# `Settings` fields, matching `agents.answer.grounding`'s own
# `_GROUNDED_THRESHOLD`/`_UNGROUNDED_THRESHOLD` precedent for a
# not-yet-empirically-calibrated scoring threshold that belongs to one
# module, not global app configuration.
_PASS_RELEVANCE_THRESHOLD = 6
_PASS_CITATION_ACCURACY_THRESHOLD = 6


class EvalCaseOutcome(BaseModel):
    """One golden case's full outcome within a run -- what `runner.py` hands
    to `repository.insert_eval_case_result`, and what a caller of
    `run_evaluation` (the CLI script, a future test) can inspect without a
    DB round-trip.

    Mirrors `app.database.models.evaluation_models.EvalCaseResult`'s columns
    exactly (see that model's docstring: "every score column is nullable: a
    case can fail before the judge ever runs ... the row still gets
    written -- `passed=False`, every score `NULL`"). `error_detail` is the
    one field with no ORM counterpart's exact equivalent in `JudgeResult`;
    it is set only when the whole case failed before/during judging (the
    `answer_question` call itself raised, or the judge call raised something
    `JudgeResult.parse_error` doesn't already cover).
    """

    model_config = ConfigDict(frozen=True)

    case_id: str
    question: str
    route_taken: str | None = None
    confidence_score: float | None = None
    citation_count: int = 0
    expected_sources: list[str] = Field(default_factory=list)
    actual_sources: list[str] = Field(default_factory=list)
    relevance_score: int | None = None
    citation_accuracy_score: int | None = None
    completeness_score: int | None = None
    grounded: bool | None = None
    hallucination_flag: bool = False
    passed: bool = False
    judge_reasoning: str | None = None
    error_detail: str | None = None


class EvalRunSummary(BaseModel):
    """What `runner.run_evaluation` returns and `scripts/run_evaluation.py`
    prints -- the run-level aggregate (mirrors `EvalRun`'s own columns) plus
    every individual `EvalCaseOutcome`, so a caller can see both "did quality
    regress overall" and "which specific question regressed."
    """

    model_config = ConfigDict(frozen=True)

    eval_run_id: uuid.UUID
    organization_id: uuid.UUID
    model_used: str
    git_commit: str | None
    case_count: int
    passed_count: int
    hallucination_count: int
    avg_relevance_score: float | None
    avg_citation_accuracy_score: float | None
    avg_confidence_score: float | None
    status: str
    cases: list[EvalCaseOutcome] = Field(default_factory=list)
