"""Unit tests for `app.evaluation.runner.run_evaluation` -- the harness's
central orchestration. Fakes every true I/O boundary (`answer_question`,
`judge_answer`, `get_llm`, `_resolve_actual_sources`, and every
`app.evaluation.repository` function) via monkeypatch, so these tests
exercise the real per-case control flow (success / answer_question failure /
investigation route / judging failure / genuine infra failure) without a
real DB, LLM, or retrieval pipeline -- the same "fake the true I/O edge, run
the real logic" approach `tests/agents/answer/test_grounding.py`/
`test_answer_question_integration.py` already establish.

The single most important behavior under test, called out explicitly in
`app.database.models.evaluation_models.EvalCaseResult`'s own docstring: a
per-case failure must never abort the run -- it degrades to a written,
all-null-scored row, and the run still completes `succeeded`.
"""

from __future__ import annotations

import uuid

import pytest

from app.database.models.evaluation_models import EvalCaseResult, EvalRun
from app.evaluation import repository, runner
from app.evaluation.schemas import GoldenCase, JudgeResult
from app.shared.schemas import AskResponse, Citation, Identity


def _actor() -> Identity:
    return Identity.for_agent("evaluation_harness", uuid.uuid4())


def _case(case_id: str = "case-1", question: str = "How do checkout 500s get handled?") -> GoldenCase:
    return GoldenCase(case_id=case_id, question=question, expected_sources=["runbooks"])


def _citation() -> Citation:
    return Citation(
        document_id=uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        source_url="https://runbooks.example/checkout",
        excerpt="Restart the checkout pod when it 500s.",
    )


def _judge_result(**overrides: object) -> JudgeResult:
    defaults: dict[str, object] = dict(
        relevance_score=8,
        citation_accuracy_score=7,
        completeness_score=8,
        grounded=True,
        hallucination_flag=False,
        reasoning="Well supported.",
    )
    defaults.update(overrides)
    return JudgeResult(**defaults)  # type: ignore[arg-type]


class _RepositoryRecorder:
    """Records every call made to the (monkeypatched) `repository` module
    functions `runner.py` calls, without touching a real DB.
    """

    def __init__(self) -> None:
        self.insert_eval_run_calls: list[dict[str, object]] = []
        self.update_eval_run_calls: list[dict[str, object]] = []
        self.insert_eval_case_result_calls: list[dict[str, object]] = []
        self.run_id = uuid.uuid4()
        self.fail_insert_case_result_on_call: int | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(repository, "insert_eval_run", self._insert_eval_run)
        monkeypatch.setattr(repository, "update_eval_run", self._update_eval_run)
        monkeypatch.setattr(repository, "insert_eval_case_result", self._insert_eval_case_result)

    async def _insert_eval_run(self, session, **kwargs: object) -> EvalRun:
        self.insert_eval_run_calls.append(kwargs)
        return EvalRun(id=self.run_id, status="running", **kwargs)  # type: ignore[arg-type]

    async def _update_eval_run(self, session, eval_run_id, **kwargs: object) -> EvalRun:
        self.update_eval_run_calls.append({"eval_run_id": eval_run_id, **kwargs})
        return EvalRun(id=eval_run_id, **kwargs)  # type: ignore[arg-type]

    async def _insert_eval_case_result(self, session, **kwargs: object) -> EvalCaseResult:
        call_number = len(self.insert_eval_case_result_calls) + 1
        self.insert_eval_case_result_calls.append(kwargs)
        if self.fail_insert_case_result_on_call == call_number:
            raise RuntimeError("db write failed")
        return EvalCaseResult(id=uuid.uuid4(), **kwargs)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_run_evaluation_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _RepositoryRecorder()
    recorder.install(monkeypatch)

    actor = _actor()
    case = _case()
    citation = _citation()
    response = AskResponse(
        confidence=0.87, route_taken="answer", answer="Restart the checkout pod [1].",
        citations=[citation],
    )
    judge_result = _judge_result()

    async def _fake_answer_question(session, question, incident_id, passed_actor, *, trigger_source):
        assert question == case.question
        assert incident_id is None
        assert passed_actor is actor
        assert trigger_source == "scheduled"
        return response

    async def _fake_judge_answer(llm, *, question, answer, context_chunks, citations):
        assert question == case.question
        assert answer == response.answer
        assert context_chunks == [citation.excerpt]
        return judge_result

    async def _fake_resolve_actual_sources(session, citations):
        return ["runbooks"]

    monkeypatch.setattr(runner, "answer_question", _fake_answer_question)
    monkeypatch.setattr(runner, "judge_answer", _fake_judge_answer)
    monkeypatch.setattr(runner, "_resolve_actual_sources", _fake_resolve_actual_sources)
    # `runner.py` now calls `get_llm("judge")` (Advanced Features Roadmap
    # Phase 1, "Model routing (2.4)") rather than the previous no-`task`
    # `get_llm(temperature=0.0)` -- this fake must accept that positional
    # argument, not just kwargs.
    monkeypatch.setattr(runner, "get_llm", lambda *args, **kwargs: object())

    summary = await runner.run_evaluation(object(), actor, [case], git_commit="deadbeef")

    assert summary.status == "succeeded"
    assert summary.case_count == 1
    assert summary.passed_count == 1
    assert summary.hallucination_count == 0
    assert summary.avg_relevance_score == 8
    assert summary.avg_citation_accuracy_score == 7
    assert summary.avg_confidence_score == 0.87
    assert summary.git_commit == "deadbeef"
    assert len(summary.cases) == 1
    assert summary.cases[0].passed is True
    assert summary.cases[0].actual_sources == ["runbooks"]

    assert len(recorder.insert_eval_run_calls) == 1
    run_call = recorder.insert_eval_run_calls[0]
    assert run_call["organization_id"] == actor.organization_id
    assert run_call["git_commit"] == "deadbeef"
    assert isinstance(run_call["model_used"], str) and run_call["model_used"]

    assert len(recorder.insert_eval_case_result_calls) == 1
    case_call = recorder.insert_eval_case_result_calls[0]
    assert case_call["case_id"] == "case-1"
    assert case_call["passed"] is True
    assert case_call["relevance_score"] == 8

    final_update = recorder.update_eval_run_calls[-1]
    assert final_update["status"] == "succeeded"
    assert final_update["case_count"] == 1
    assert final_update["passed_count"] == 1


@pytest.mark.asyncio
async def test_run_evaluation_answer_question_failure_is_recorded_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The critical regression: `answer_question` raising for one case must
    not abort the run or the other case.
    """
    recorder = _RepositoryRecorder()
    recorder.install(monkeypatch)

    actor = _actor()
    broken_case = _case(case_id="broken-case", question="This blows up.")
    good_case = _case(case_id="good-case", question="This works.")
    citation = _citation()
    good_response = AskResponse(
        confidence=0.9, route_taken="answer", answer="It works [1].", citations=[citation]
    )

    async def _fake_answer_question(session, question, incident_id, passed_actor, *, trigger_source):
        if question == broken_case.question:
            raise RuntimeError("retrieval backend unreachable")
        return good_response

    async def _fake_judge_answer(llm, *, question, answer, context_chunks, citations):
        return _judge_result(relevance_score=9, citation_accuracy_score=9)

    async def _fake_resolve_actual_sources(session, citations):
        return ["runbooks"]

    monkeypatch.setattr(runner, "answer_question", _fake_answer_question)
    monkeypatch.setattr(runner, "judge_answer", _fake_judge_answer)
    monkeypatch.setattr(runner, "_resolve_actual_sources", _fake_resolve_actual_sources)
    # `runner.py` now calls `get_llm("judge")` (Advanced Features Roadmap
    # Phase 1, "Model routing (2.4)") rather than the previous no-`task`
    # `get_llm(temperature=0.0)` -- this fake must accept that positional
    # argument, not just kwargs.
    monkeypatch.setattr(runner, "get_llm", lambda *args, **kwargs: object())

    summary = await runner.run_evaluation(object(), actor, [broken_case, good_case])

    # The run itself must succeed overall -- one bad case is not a harness failure.
    assert summary.status == "succeeded"
    assert summary.case_count == 2
    assert summary.passed_count == 1  # only the good case passed
    assert len(recorder.insert_eval_case_result_calls) == 2

    broken_call = recorder.insert_eval_case_result_calls[0]
    assert broken_call["case_id"] == "broken-case"
    assert broken_call["passed"] is False
    assert broken_call["relevance_score"] is None
    assert broken_call["citation_accuracy_score"] is None
    assert broken_call["completeness_score"] is None
    assert broken_call["grounded"] is None
    assert broken_call["hallucination_flag"] is False
    assert broken_call["actual_sources"] == []
    assert broken_call["expected_sources"] == ["runbooks"]
    assert "retrieval backend unreachable" in broken_call["error_detail"]

    good_call = recorder.insert_eval_case_result_calls[1]
    assert good_call["case_id"] == "good-case"
    assert good_call["passed"] is True

    # Aggregates must average only over the case that actually produced a score.
    assert summary.avg_relevance_score == 9


@pytest.mark.asyncio
async def test_run_evaluation_investigation_route_is_recorded_but_not_judged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _RepositoryRecorder()
    recorder.install(monkeypatch)

    actor = _actor()
    case = _case()
    investigation_response = AskResponse(confidence=0.3, route_taken="investigation", answer=None)

    judge_calls = []

    async def _fake_answer_question(session, question, incident_id, passed_actor, *, trigger_source):
        return investigation_response

    async def _fake_judge_answer(llm, **kwargs):
        judge_calls.append(kwargs)
        raise AssertionError("judge_answer must not be called for an investigation-route response")

    monkeypatch.setattr(runner, "answer_question", _fake_answer_question)
    monkeypatch.setattr(runner, "judge_answer", _fake_judge_answer)
    # `runner.py` now calls `get_llm("judge")` (Advanced Features Roadmap
    # Phase 1, "Model routing (2.4)") rather than the previous no-`task`
    # `get_llm(temperature=0.0)` -- this fake must accept that positional
    # argument, not just kwargs.
    monkeypatch.setattr(runner, "get_llm", lambda *args, **kwargs: object())

    summary = await runner.run_evaluation(object(), actor, [case])

    assert judge_calls == []
    assert summary.status == "succeeded"
    assert summary.cases[0].route_taken == "investigation"
    assert summary.cases[0].passed is False
    assert summary.cases[0].judge_reasoning is not None
    assert "investigation" in summary.cases[0].judge_reasoning.lower()


@pytest.mark.asyncio
async def test_run_evaluation_judging_failure_is_recorded_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _RepositoryRecorder()
    recorder.install(monkeypatch)

    actor = _actor()
    case = _case()
    citation = _citation()
    response = AskResponse(
        confidence=0.8, route_taken="answer", answer="An answer [1].", citations=[citation]
    )

    async def _fake_answer_question(session, question, incident_id, passed_actor, *, trigger_source):
        return response

    async def _fake_resolve_actual_sources(session, citations):
        raise RuntimeError("document lookup failed")

    monkeypatch.setattr(runner, "answer_question", _fake_answer_question)
    monkeypatch.setattr(runner, "_resolve_actual_sources", _fake_resolve_actual_sources)
    # `runner.py` now calls `get_llm("judge")` (Advanced Features Roadmap
    # Phase 1, "Model routing (2.4)") rather than the previous no-`task`
    # `get_llm(temperature=0.0)` -- this fake must accept that positional
    # argument, not just kwargs.
    monkeypatch.setattr(runner, "get_llm", lambda *args, **kwargs: object())

    summary = await runner.run_evaluation(object(), actor, [case])

    assert summary.status == "succeeded"
    assert summary.cases[0].passed is False
    assert summary.cases[0].error_detail is not None
    assert "document lookup failed" in summary.cases[0].error_detail


@pytest.mark.asyncio
async def test_run_evaluation_infra_failure_marks_run_failed_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine infrastructure failure (here: the DB write for a case
    result itself raising) is NOT the same as a per-case scoring failure --
    it must mark the run `failed` and re-raise, matching
    `agents.service.detect_knowledge_gaps`'s equivalent failure handling.
    """
    recorder = _RepositoryRecorder()
    recorder.fail_insert_case_result_on_call = 1
    recorder.install(monkeypatch)

    actor = _actor()
    case = _case()
    citation = _citation()
    response = AskResponse(
        confidence=0.8, route_taken="answer", answer="An answer [1].", citations=[citation]
    )

    async def _fake_answer_question(session, question, incident_id, passed_actor, *, trigger_source):
        return response

    async def _fake_judge_answer(llm, **kwargs):
        return _judge_result()

    async def _fake_resolve_actual_sources(session, citations):
        return ["runbooks"]

    monkeypatch.setattr(runner, "answer_question", _fake_answer_question)
    monkeypatch.setattr(runner, "judge_answer", _fake_judge_answer)
    monkeypatch.setattr(runner, "_resolve_actual_sources", _fake_resolve_actual_sources)
    # `runner.py` now calls `get_llm("judge")` (Advanced Features Roadmap
    # Phase 1, "Model routing (2.4)") rather than the previous no-`task`
    # `get_llm(temperature=0.0)` -- this fake must accept that positional
    # argument, not just kwargs.
    monkeypatch.setattr(runner, "get_llm", lambda *args, **kwargs: object())

    with pytest.raises(RuntimeError, match="db write failed"):
        await runner.run_evaluation(object(), actor, [case])

    assert recorder.update_eval_run_calls[-1]["status"] == "failed"
    assert "db write failed" in recorder.update_eval_run_calls[-1]["error_detail"]


def test_average_ignores_none_and_handles_all_none() -> None:
    assert runner._average([1, 2, 3]) == 2
    assert runner._average([None, None]) is None
    assert runner._average([5, None, 7]) == 6
    assert runner._average([]) is None


@pytest.mark.asyncio
async def test_resolve_actual_sources_returns_empty_list_for_no_citations() -> None:
    assert await runner._resolve_actual_sources(object(), []) == []  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_resolve_actual_sources_dedupes_and_sorts(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeScalars:
        def all(self) -> list[str]:
            return ["slack", "github", "slack"]

    class _FakeResult:
        def scalars(self) -> _FakeScalars:
            return _FakeScalars()

    class _FakeSession:
        async def execute(self, statement: object) -> _FakeResult:
            return _FakeResult()

    citations = [_citation(), _citation()]

    result = await runner._resolve_actual_sources(_FakeSession(), citations)  # type: ignore[arg-type]

    assert result == sorted({"slack", "github", "slack"})
