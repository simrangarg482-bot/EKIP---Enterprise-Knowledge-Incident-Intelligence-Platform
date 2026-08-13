"""Evaluation harness for EKIP's confidence-gated routing (`app.agents.
confidence`).

WHAT THIS IS
    Runs every question in `eval_confidence_dataset.json` through the real,
    unmodified production entry point (`app.agents.service.answer_question`)
    against the real data already ingested into the `test-org` organization
    -- the same corpus `tests/rag_validation` uses. For each question it
    records the actual `confidence_score`, the `route_taken` the current
    `Settings.confidence_threshold` produced, and the generated answer.

    It then sweeps a set of *candidate* thresholds (0.40 through 0.80) and
    computes precision/recall for the answer-vs-investigate decision at each
    one, entirely from the confidence scores already recorded -- no need to
    re-run the graph per threshold, because `evaluate_confidence` computes
    `confidence_score` independently of `threshold`; only the *route*
    decision (`"answer" if confidence_score >= threshold else
    "investigation"`) depends on it (see `app/agents/confidence.py`
    `evaluate_confidence` and `app/agents/graph.py` `_route_after_confidence`
    -- both compare the same already-computed score against a threshold, so
    replaying that comparison here for a different threshold is exactly what
    the real graph would have done).

WHY PRECISION/RECALL, AND AGAINST WHAT GROUND TRUTH
    This is framed as binary classification: the positive class is
    "answer". Ground truth (`expected_route` in the dataset) is "answer"
    only for `clear-answer` questions -- ones the corpus states plainly.
    Both `ambiguous` (a relevant-looking chunk exists but doesn't support
    the specific fact asked) and `no-information` (nothing relevant exists)
    questions have ground truth "investigation": per
    `tests/rag_validation/README.md`'s own framing, a system that answers
    confidently on either is "the single most damaging failure mode for a
    system like this" -- a confidently wrong answer -- so both belong in
    the negative class for this decision, even though they are different
    *reasons* to decline.

      TP = clear-answer question, predicted "answer"        (correct)
      FN = clear-answer question, predicted "investigation" (overly cautious)
      FP = ambiguous/no-information question, predicted "answer" (confidently wrong)
      TN = ambiguous/no-information question, predicted "investigation" (correct)

      precision = TP / (TP + FP)   -- of what it answered, how much was earned
      recall    = TP / (TP + FN)   -- of what it could have answered, how much it did

WHY IT IS A SCRIPT, NOT A pytest TEST
    Same reasoning as `tests/rag_validation/run_validation.py`: it needs a
    live database with real ingested data and a real `OPENAI_API_KEY`, and
    costs real money and minutes per run. `pyproject.toml` sets `testpaths =
    ["tests"]`, and this file also lives outside `tests/` entirely (in
    `scripts/`, alongside this project's other manually-run operational
    scripts) rather than being collected by accident.

RUN
    python scripts/eval_confidence.py
    python scripts/eval_confidence.py --category ambiguous
    python scripts/eval_confidence.py --limit 5
    python scripts/eval_confidence.py --thresholds 0.5,0.6,0.7
    python scripts/eval_confidence.py --report-path scripts/eval_confidence_report.json
    python scripts/eval_confidence.py --compare-to scripts/eval_confidence_report_before.json

SIDE EFFECTS ON THE LIVE DATABASE
    `answer_question` records one `agent_executions` row per question (its
    normal production behavior). Nothing else is written.

CHANGING THE DEFAULT THRESHOLD
    Per this script's own purpose, `Settings.confidence_threshold`'s default
    in `app/shared/config/settings.py` should only change once a real run of
    this script supports a specific number -- and the commit doing so should
    cite the run's date and results in a comment there, not just a vibe.

CATEGORY-LEVEL BEHAVIOR METRICS (added alongside the threshold-vs-confidence
investigation that motivated it: `AskResponse.route_taken == "answer"` is
decided by the confidence gate BEFORE generation runs, and stays "answer"
even when the Answer Agent (`agents.answer.node`) later exhausts its own
retries and falls back to an explicit "insufficient grounded information"
decline -- the threshold sweep above, which only reads `route_taken`, cannot
tell a real answer apart from that mislabeled decline. `_is_real_answer`
below checks the actual delivered content, not just the route label, and
backs four additional metrics:
  - clear-answer accuracy         -- of `clear-answer` questions, how many
    got a real, cited answer.
  - ambiguous false-answer rate   -- of `ambiguous` questions (topically
    relevant evidence exists but doesn't state the specific fact asked), how
    many got a real, cited answer anyway -- a confidently wrong answer.
  - no-information investigation rate -- of `no-information` questions, how
    many did NOT get a real answer (correctly declined, by either route).
  - answer-grounding rate         -- separate from the three above and
    measured differently: a direct, single-pass, non-retried trace of
    `rewrite_query` -> `retrieval.service.search` -> `rerank` ->
    `assemble_context` -> `generate_answer` -> `verify_grounding` (bypassing
    `agents.service.answer_question`'s retry loop and any pre-generation
    gate entirely), reporting what fraction of the raw generated answer's
    sentences survive grounding. This is a control measurement of
    `agents.answer.grounding`'s own quality in isolation -- it should stay
    essentially constant across a change that only decides *whether* to
    attempt/serve generation, not one that touches grounding itself; if it
    moves, something changed grounding.py or generation.py, which is worth
    flagging on its own.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Same reasoning as tests/rag_validation/run_validation.py: SQLAlchemy's
# echo=True buries this harness's report under statement dumps, and setting
# it here (before the engine is lazily constructed on first use) is the only
# point at which it actually takes effect.
def _silence(*logger_names: str) -> None:
    for name in logger_names:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.disabled = True


_silence("sqlalchemy.engine", "sqlalchemy.engine.Engine", "sqlalchemy.pool", "httpx", "openai")

from sqlalchemy import select  # noqa: E402

from app.agents import service as agents_service  # noqa: E402
from app.agents.answer.generation import generate_answer, is_no_answer  # noqa: E402
from app.agents.answer.grounding import split_sentences, verify_grounding  # noqa: E402
from app.agents.llm import get_llm  # noqa: E402
from app.agents.retrieval.context_assembly import assemble_context  # noqa: E402
from app.agents.retrieval.reranking import rerank  # noqa: E402
from app.agents.retrieval.rewriting import rewrite_query  # noqa: E402
from app.database.models.ingestion_models import Document  # noqa: E402
from app.database.models.tenancy_models import Organization  # noqa: E402
from app.database.session import session_scope, set_tenant_context  # noqa: E402
from app.retrieval import service as retrieval_service  # noqa: E402
from app.retrieval.schemas import SearchFilters  # noqa: E402
from app.shared.config.settings import get_settings  # noqa: E402
from app.shared.schemas import Identity  # noqa: E402

_DATASET_PATH = Path(__file__).resolve().parent / "eval_confidence_dataset.json"
_DEFAULT_ORG_SLUG = "test-org"
_DEFAULT_REPORT_PATH = Path(__file__).resolve().parent / "eval_confidence_report.json"

# Matches app.agents.retrieval.node's own _CANDIDATE_POOL_SIZE/_RERANKED_TOP_K
# -- duplicated, not imported, same "cross-module constant, kept in sync by
# hand" precedent as this file's other duplicated constants (see
# `_DECLINE_MESSAGE` below), so the direct trace below sees the same
# candidate-pool/rerank depth the production Retrieval Agent actually uses.
_TRACE_CANDIDATE_POOL_SIZE = 40
_TRACE_RERANK_TOP_K = 20

# Must match `app.agents.answer.node._INSUFFICIENT_GROUNDING_MESSAGE`
# exactly -- duplicated rather than imported (that name is module-private,
# and this codebase's own convention for a cross-module constant it can't
# import is to duplicate it with a comment, e.g. `retrieval.embedding.
# EMBEDDING_DIMENSION`). Needed to tell a real answer apart from a
# route="answer"-labeled decline -- see this module's docstring.
_DECLINE_MESSAGE = (
    "I don't have enough grounded information from the available sources to answer this confidently."
)

# "0.4 through 0.8" per the task -- fine enough granularity to locate a
# clear winner without pretending precision finer than 0.05 is meaningful
# against a 36-question dataset.
_CANDIDATE_THRESHOLDS: list[float] = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80]

# Categories whose ground-truth route is "answer" -- every other category
# (ambiguous, no-information) is ground-truth "investigation". See module
# docstring's "WHY PRECISION/RECALL" section.
_POSITIVE_CATEGORIES = frozenset({"clear-answer"})

# Minimum F1 improvement over the current default before this harness's own
# summary calls it a real result rather than noise -- picked to be well
# above the size of a single question flipping categories on a ~36-question
# dataset (1/36 ~= 0.03 swing in raw counts, which moves f1 by more than
# this on its own), so a margin below this is indistinguishable from "one
# question could have gone either way," not a real signal.
_MARGIN_FOR_CHANGE = 0.05


# --------------------------------------------------------------------------
# data model
# --------------------------------------------------------------------------


class QuestionResult:
    def __init__(
        self,
        entry: dict,
        confidence: float,
        route_taken: str,
        answer: str | None,
        citations_count: int,
        error: str | None,
    ):
        self.entry = entry
        self.confidence = confidence
        self.route_taken = route_taken
        self.answer = answer
        self.citations_count = citations_count
        self.error = error

    @property
    def expected_route(self) -> str:
        return self.entry["expected_route"]

    @property
    def is_positive(self) -> bool:
        """Ground truth: should this question have been answered directly?"""
        return self.entry["category"] in _POSITIVE_CATEGORIES

    @property
    def is_real_answer(self) -> bool:
        """Whether the pipeline actually delivered a real, cited answer --
        NOT the same question as `route_taken == "answer"`. See this
        module's docstring: the Answer Agent can fall back to
        `_DECLINE_MESSAGE` while `route_taken` still reads "answer" (the
        route is decided before generation runs), so a category-level
        "did it actually answer" metric must check the delivered content,
        not just the route label. Requires both a non-decline answer text
        AND at least one citation -- a real grounded answer's generation
        prompt requires every factual claim to carry a `[n]` citation
        marker, so an answer with zero citations is not a case this metric
        should count as "confidently answered" even if it slipped through.
        """
        if self.error is not None or self.route_taken != "answer" or not self.answer:
            return False
        return self.answer.strip() != _DECLINE_MESSAGE and self.citations_count > 0

    def predicted_route(self, threshold: float) -> str:
        """What `_route_after_confidence` would have chosen at `threshold`,
        replaying the exact comparison `evaluate_confidence` makes -- see
        module docstring for why this is valid without re-running the graph.
        Only call this on a result with no `error` -- `confidence` is `None`
        otherwise.
        """
        assert self.confidence is not None, "predicted_route() called on an errored result"
        return "answer" if self.confidence >= threshold else "investigation"


# --------------------------------------------------------------------------
# running the real pipeline
# --------------------------------------------------------------------------


async def _resolve_org(slug: str) -> tuple[uuid.UUID, str, int]:
    """Same purpose as `run_validation.py`'s `_resolve_org`: fail loudly on
    an empty organization instead of silently reporting every question as
    "no information found" and looking like a routing failure.
    """
    async with session_scope() as session:
        row = (
            await session.execute(select(Organization).where(Organization.slug == slug))
        ).scalar_one_or_none()
        if row is None:
            raise SystemExit(f"No organization with slug {slug!r} exists in this database.")
        await set_tenant_context(session, row.id)
        doc_count = len(
            (
                await session.execute(
                    select(Document.id).where(
                        Document.organization_id == row.id, Document.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        return row.id, row.name, doc_count


_MAX_HARNESS_RETRIES = 2  # extra attempts on top of the first, for transient network/API errors


async def _run_question(entry: dict, organization_id: uuid.UUID) -> QuestionResult:
    """Calls the real pipeline, retrying transient harness-level failures
    (a flaky network hop, a dropped DNS lookup) a couple of times before
    giving up -- distinct from `agents.retry`'s own per-node retries inside
    the graph, which already ran and were already exhausted by the time an
    exception reaches here.

    On a final failure, `error` is set and `confidence`/`route_taken` are
    left `None` -- NOT defaulted to some placeholder value. Defaulting a
    failed call to `route_taken="investigation"` would silently count as a
    correct prediction for every `ambiguous`/`no-information` question
    (whose ground truth already is "investigation") without the pipeline
    having actually been evaluated at all -- fabricated evidence, exactly
    what this harness exists to avoid. `_confusion_at` skips any result with
    a non-`None` error entirely rather than guessing.
    """
    actor = Identity.for_agent("eval_confidence", organization_id)
    last_error: str | None = None
    for attempt in range(_MAX_HARNESS_RETRIES + 1):
        try:
            async with session_scope() as session:
                await set_tenant_context(session, organization_id)
                ask = await agents_service.answer_question(session, entry["question"], None, actor)
            return QuestionResult(
                entry, ask.confidence, ask.route_taken, ask.answer, len(ask.citations), error=None
            )
        except Exception as exc:  # noqa: BLE001 - one bad question must not abort the run
            last_error = str(exc)
            if attempt < _MAX_HARNESS_RETRIES:
                await asyncio.sleep(2 * (attempt + 1))
    return QuestionResult(
        entry, confidence=None, route_taken=None, answer=None, citations_count=0, error=last_error
    )


async def _run_grounding_trace(entry: dict, organization_id: uuid.UUID, llm) -> dict:
    """One direct, single-pass, non-retried trace of the component pipeline
    (bypassing `agents.service.answer_question`'s retry loop and any
    pre-generation gate entirely) -- see this module's docstring for why
    this is a deliberately separate measurement from `_run_question`'s.
    """
    actor = Identity.for_agent("eval_confidence_grounding", organization_id)
    try:
        async with session_scope() as session:
            await set_tenant_context(session, organization_id)
            rewritten = await rewrite_query(
                session, query=entry["question"], incident_id=None, actor=actor, llm=llm, retry_count={}
            )
            filters = SearchFilters(organization_id=organization_id, permission_codes=frozenset())
            candidates = await retrieval_service.search(
                session, rewritten, filters, _TRACE_CANDIDATE_POOL_SIZE
            )
            reranked = await rerank(rewritten, candidates, top_k=_TRACE_RERANK_TOP_K)
            chunks = assemble_context(reranked)

        if not chunks:
            return {"id": entry["id"], "category": entry["category"], "sentence_count": 0, "grounded_count": 0, "grounded_ratio": None, "error": None}

        raw_answer = await generate_answer(llm, entry["question"], chunks)
        if is_no_answer(raw_answer):
            return {"id": entry["id"], "category": entry["category"], "sentence_count": 0, "grounded_count": 0, "grounded_ratio": 0.0, "error": None}

        sentences = split_sentences(raw_answer)
        grounded_sentences = await verify_grounding(llm, sentences, chunks)
        ratio = len(grounded_sentences) / len(sentences) if sentences else 0.0
        return {
            "id": entry["id"],
            "category": entry["category"],
            "sentence_count": len(sentences),
            "grounded_count": len(grounded_sentences),
            "grounded_ratio": ratio,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - one bad question must not abort the run
        return {"id": entry["id"], "category": entry["category"], "sentence_count": 0, "grounded_count": 0, "grounded_ratio": None, "error": str(exc)}


# --------------------------------------------------------------------------
# threshold sweep
# --------------------------------------------------------------------------


def _confusion_at(results: list[QuestionResult], threshold: float) -> dict[str, int]:
    """Excludes any result with `error` set -- a harness/network failure is
    not evidence about the confidence gate one way or the other, so it must
    not be silently folded into the confusion matrix (see `_run_question`'s
    docstring).
    """
    tp = fp = fn = tn = 0
    for result in results:
        if result.error is not None:
            continue
        predicted_answer = result.predicted_route(threshold) == "answer"
        if result.is_positive:
            tp += predicted_answer
            fn += not predicted_answer
        else:
            fp += predicted_answer
            tn += not predicted_answer
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def _category_behavior_metrics(evaluated: list[QuestionResult]) -> dict[str, dict]:
    """Per-category `is_real_answer` rate, plus the three named headline
    metrics derived from it -- see this module's docstring.
    """
    by_category: dict[str, list[QuestionResult]] = defaultdict(list)
    for result in evaluated:
        by_category[result.entry["category"]].append(result)

    per_category = {
        category: {
            "count": len(results),
            "real_answer_rate": sum(r.is_real_answer for r in results) / len(results),
        }
        for category, results in by_category.items()
    }

    clear = per_category.get("clear-answer")
    ambiguous = per_category.get("ambiguous")
    no_info = per_category.get("no-information")
    return {
        "per_category": per_category,
        "clear_answer_accuracy": clear["real_answer_rate"] if clear else None,
        "ambiguous_false_answer_rate": ambiguous["real_answer_rate"] if ambiguous else None,
        "no_information_investigation_rate": (1 - no_info["real_answer_rate"]) if no_info else None,
    }


def _grounding_rate_metrics(traces: list[dict]) -> dict[str, dict]:
    """Per-category mean sentence-grounded-ratio from `_run_grounding_trace`
    results, excluding any trace that errored.
    """
    by_category: dict[str, list[dict]] = defaultdict(list)
    for trace in traces:
        if trace["error"] is None and trace["grounded_ratio"] is not None:
            by_category[trace["category"]].append(trace)

    per_category = {
        category: sum(t["grounded_ratio"] for t in traces_) / len(traces_)
        for category, traces_ in by_category.items()
        if traces_
    }
    return {
        "per_category": per_category,
        "headline_answer_grounding_rate": per_category.get("clear-answer"),
    }


def _precision_recall_f1(confusion: dict[str, int]) -> dict[str, float | None]:
    tp, fp, fn, tn = confusion["tp"], confusion["fp"], confusion["fn"], confusion["tn"]
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * precision * recall / (precision + recall)) if (precision and recall) else None
    accuracy = (tp + tn) / (tp + fp + fn + tn) if (tp + fp + fn + tn) else None
    return {"precision": precision, "recall": recall, "f1": f1, "accuracy": accuracy}


def _sweep(results: list[QuestionResult], thresholds: list[float]) -> list[dict]:
    rows = []
    for threshold in thresholds:
        confusion = _confusion_at(results, threshold)
        metrics = _precision_recall_f1(confusion)
        rows.append({"threshold": threshold, **confusion, **metrics})
    return rows


def _pick_best(sweep_rows: list[dict]) -> dict:
    """Highest F1 wins; ties broken toward the *higher* threshold -- i.e.
    toward fewer false positives / higher precision. A confidently wrong
    answer (FP) is the worse failure mode of the two (see module docstring's
    citation of tests/rag_validation/README.md), so when two thresholds tie
    on F1 the more conservative one is the better tie-break, not an
    arbitrary one.
    """
    scored = [row for row in sweep_rows if row["f1"] is not None]
    if not scored:
        return max(sweep_rows, key=lambda row: (row["precision"] or 0, row["threshold"]))
    return max(scored, key=lambda row: (row["f1"], row["threshold"]))


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def _print_question_row(result: QuestionResult, current_threshold: float) -> None:
    entry = result.entry
    if result.error is not None:
        print(
            f"  [ERR ] {entry['id']:<32} {entry['category']:<15} "
            f"expected={result.expected_route:<12} actual=n/a          confidence=n/a",
            flush=True,
        )
        print(f"         ERROR (excluded from metrics): {result.error}", flush=True)
        return

    matches_expected = result.route_taken == result.expected_route
    marker = "OK  " if matches_expected else "MISS"
    print(
        f"  [{marker}] {entry['id']:<32} {entry['category']:<15} "
        f"expected={result.expected_route:<12} actual={result.route_taken:<12} "
        f"confidence={result.confidence:.3f}",
        flush=True,
    )
    if result.answer:
        print(f"         answer: {result.answer.strip()[:160]}", flush=True)


def _print_sweep_table(sweep_rows: list[dict], current_threshold: float, best: dict) -> None:
    print("\nThreshold sweep (answer-vs-investigate decision):", flush=True)
    print(
        f"  {'threshold':>9}  {'TP':>3} {'FP':>3} {'FN':>3} {'TN':>3}  "
        f"{'precision':>9} {'recall':>9} {'f1':>9} {'accuracy':>9}",
        flush=True,
    )
    for row in sweep_rows:
        flags = []
        if row["threshold"] == current_threshold:
            flags.append("current default")
        if row["threshold"] == best["threshold"]:
            flags.append("best")
        flag_str = f"  <- {', '.join(flags)}" if flags else ""

        def _fmt(value: float | None) -> str:
            return f"{value:.3f}" if value is not None else "n/a"

        print(
            f"  {row['threshold']:>9.2f}  {row['tp']:>3} {row['fp']:>3} {row['fn']:>3} {row['tn']:>3}  "
            f"{_fmt(row['precision']):>9} {_fmt(row['recall']):>9} {_fmt(row['f1']):>9} "
            f"{_fmt(row['accuracy']):>9}{flag_str}",
            flush=True,
        )


def _confidence_by_category(evaluated: list[QuestionResult]) -> dict[str, dict]:
    by_category: dict[str, list[float]] = defaultdict(list)
    for result in evaluated:
        by_category[result.entry["category"]].append(result.confidence)
    return {
        category: {"min": min(scores), "max": max(scores), "mean": sum(scores) / len(scores), "n": len(scores)}
        for category, scores in by_category.items()
    }


def _print_confidence_distribution(confidence_by_category: dict[str, dict]) -> None:
    """Per-category confidence range, and whether `clear-answer` and
    `ambiguous` overlap. This overlap is the reason no single threshold in
    `_CANDIDATE_THRESHOLDS` can cleanly separate them: `ambiguous` questions
    retrieve a topically-relevant chunk (high `rerank_score`) that simply
    doesn't contain the specific fact asked -- confidence, as currently
    computed, measures topical relevance, not whether the *specific* fact is
    present, so it cannot tell the two apart. That is a signal-quality gap,
    not a threshold-choice one -- no amount of threshold sweeping fixes it.
    """
    print("\nConfidence distribution by category:", flush=True)
    for category, stats in confidence_by_category.items():
        print(
            f"  {category:<15} min={stats['min']:.3f}  max={stats['max']:.3f}  "
            f"mean={stats['mean']:.3f}  (n={stats['n']})",
            flush=True,
        )
    clear, ambiguous = confidence_by_category.get("clear-answer"), confidence_by_category.get("ambiguous")
    if clear and ambiguous and clear["min"] <= ambiguous["max"] and ambiguous["min"] <= clear["max"]:
        print(
            "  NOTE: clear-answer and ambiguous confidence ranges overlap -- no threshold in "
            "this sweep (or any other) can perfectly separate them; see this function's "
            "docstring.",
            flush=True,
        )


def _print_category_behavior(behavior: dict) -> None:
    print("\nCategory behavior metrics:", flush=True)

    def _fmt(value: float | None) -> str:
        return f"{value:.3f}" if value is not None else "n/a"

    print(f"  clear-answer accuracy            : {_fmt(behavior['clear_answer_accuracy'])}", flush=True)
    print(f"  ambiguous false-answer rate      : {_fmt(behavior['ambiguous_false_answer_rate'])}", flush=True)
    print(
        f"  no-information investigation rate: {_fmt(behavior['no_information_investigation_rate'])}",
        flush=True,
    )


def _print_grounding_rate(grounding: dict) -> None:
    print("\nAnswer-grounding rate (direct trace, per category):", flush=True)
    for category, value in grounding["per_category"].items():
        print(f"  {category:<15} {value:.3f}", flush=True)


def _build_report(
    org_name: str,
    org_slug: str,
    results: list[QuestionResult],
    sweep_rows: list[dict],
    best: dict,
    current_threshold: float,
    behavior: dict,
    grounding: dict,
    grounding_traces: list[dict],
    confidence_by_category: dict[str, dict],
) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "organization": {"slug": org_slug, "name": org_name},
        "current_default_threshold": current_threshold,
        "excluded_count": sum(1 for r in results if r.error is not None),
        "best_threshold": best["threshold"],
        "best_threshold_metrics": {
            "precision": best["precision"],
            "recall": best["recall"],
            "f1": best["f1"],
            "accuracy": best["accuracy"],
        },
        "threshold_sweep": sweep_rows,
        "category_behavior": behavior,
        "grounding": grounding,
        "confidence_by_category": confidence_by_category,
        "questions": [
            {
                "id": r.entry["id"],
                "category": r.entry["category"],
                "expected_route": r.expected_route,
                "actual_route_at_current_default": r.route_taken,
                "confidence_score": r.confidence,
                "is_real_answer": r.is_real_answer if r.error is None else None,
                "citations_count": r.citations_count,
                "answer": r.answer,
                "error": r.error,
            }
            for r in results
        ],
        "grounding_traces": grounding_traces,
    }


# --------------------------------------------------------------------------
# before/after comparison
# --------------------------------------------------------------------------


def _load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _compare_reports(before: dict, after: dict) -> None:
    """Prints a before/after diff for every headline metric this harness
    tracks, explicitly flagging any metric that got worse -- per this
    task's own instruction not to declare success from an aggregate score
    without checking every category for regressions.
    """
    print("\n" + "=" * 78, flush=True)
    print("BEFORE / AFTER COMPARISON", flush=True)
    print("=" * 78, flush=True)
    print(f"  before: {before.get('generated_at', 'unknown')}", flush=True)
    print(f"  after : {after.get('generated_at', 'unknown')}", flush=True)

    rows: list[tuple[str, float | None, float | None, bool]] = []

    def _add(label: str, before_value, after_value, higher_is_better: bool = True) -> None:
        rows.append((label, before_value, after_value, higher_is_better))

    before_behavior, after_behavior = before.get("category_behavior", {}), after.get("category_behavior", {})
    _add("clear-answer accuracy", before_behavior.get("clear_answer_accuracy"), after_behavior.get("clear_answer_accuracy"))
    _add(
        "ambiguous false-answer rate",
        before_behavior.get("ambiguous_false_answer_rate"),
        after_behavior.get("ambiguous_false_answer_rate"),
        higher_is_better=False,
    )
    _add(
        "no-information investigation rate",
        before_behavior.get("no_information_investigation_rate"),
        after_behavior.get("no_information_investigation_rate"),
    )

    before_grounding, after_grounding = before.get("grounding", {}), after.get("grounding", {})
    _add(
        "answer-grounding rate (clear-answer)",
        before_grounding.get("headline_answer_grounding_rate"),
        after_grounding.get("headline_answer_grounding_rate"),
    )
    for category in ("clear-answer", "ambiguous", "no-information"):
        _add(
            f"    grounding rate [{category}]",
            (before_grounding.get("per_category") or {}).get(category),
            (after_grounding.get("per_category") or {}).get(category),
        )

    before_conf, after_conf = before.get("confidence_by_category", {}), after.get("confidence_by_category", {})
    for category in ("clear-answer", "ambiguous", "no-information"):
        _add(
            f"    confidence mean [{category}]",
            (before_conf.get(category) or {}).get("mean"),
            (after_conf.get(category) or {}).get("mean"),
            higher_is_better=None,  # informational only -- see printed note below
        )

    _add(
        "best-threshold f1 (threshold sweep)",
        before.get("best_threshold_metrics", {}).get("f1"),
        after.get("best_threshold_metrics", {}).get("f1"),
    )

    print(f"\n  {'metric':<40} {'before':>10} {'after':>10}  {'':>12}", flush=True)
    for label, before_value, after_value, higher_is_better in rows:
        before_str = f"{before_value:.3f}" if before_value is not None else "n/a"
        after_str = f"{after_value:.3f}" if after_value is not None else "n/a"
        flag = ""
        if higher_is_better is not None and before_value is not None and after_value is not None:
            delta = after_value - before_value
            if higher_is_better and delta < -1e-9:
                flag = "REGRESSION"
            elif not higher_is_better and delta > 1e-9:
                flag = "REGRESSION"
        elif higher_is_better is None:
            flag = "(informational -- confidence.py was not touched; should be ~unchanged)"
        print(f"  {label:<40} {before_str:>10} {after_str:>10}  {flag}", flush=True)

    regressions = [label for label, b, a, hib in rows if hib is not None and b is not None and a is not None and ((hib and a < b - 1e-9) or (not hib and a > b + 1e-9))]
    if regressions:
        print(
            f"\n  REGRESSIONS DETECTED in: {regressions} -- do not treat this change as a net "
            "improvement without accounting for these; see the per-category breakdown above.",
            flush=True,
        )
    else:
        print("\n  No regressions on any tracked metric.", flush=True)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


async def _run(args) -> dict:
    dataset = json.loads(_DATASET_PATH.read_text(encoding="utf-8"))
    questions = dataset["questions"]
    if args.category:
        questions = [q for q in questions if q["category"] == args.category]
    if args.limit:
        questions = questions[: args.limit]

    org_id, org_name, doc_count = await _resolve_org(args.org_slug)
    current_threshold = get_settings().confidence_threshold

    print("=" * 78, flush=True)
    print("EKIP -- CONFIDENCE-GATED ROUTING EVALUATION", flush=True)
    print("=" * 78, flush=True)
    print(f"Organization          : {org_name!r} (slug={args.org_slug}, id={org_id})", flush=True)
    print(f"Live documents in org  : {doc_count}", flush=True)
    print(f"Questions to run       : {len(questions)}", flush=True)
    print(f"Current default threshold : {current_threshold}", flush=True)
    print(f"Candidate thresholds   : {args.thresholds}", flush=True)
    if doc_count == 0:
        raise SystemExit(
            f"\nOrganization {args.org_slug!r} has 0 ingested documents -- every question would "
            "trivially route to investigation for lack of data. Point --org-slug at the "
            "organization your connectors actually ingested into."
        )

    print("\nPer-question results:", flush=True)
    results: list[QuestionResult] = []
    for entry in questions:
        result = await _run_question(entry, org_id)
        _print_question_row(result, current_threshold)
        results.append(result)

    grounding_traces: list[dict] = []
    if not args.skip_grounding_trace:
        print("\nGrounding trace (direct, single-pass, bypasses retries/gates):", flush=True)
        llm = get_llm(temperature=0.0)
        for entry in questions:
            trace = await _run_grounding_trace(entry, org_id, llm)
            grounding_traces.append(trace)
            ratio = trace["grounded_ratio"]
            ratio_str = f"{ratio:.3f}" if ratio is not None else "n/a (error)"
            print(f"  [{entry['id']:<32}] grounded_ratio={ratio_str}", flush=True)

    sweep_rows = _sweep(results, args.thresholds)
    best = _pick_best(sweep_rows)
    _print_sweep_table(sweep_rows, current_threshold, best)

    current_confusion = _confusion_at(results, current_threshold)
    current_metrics = _precision_recall_f1(current_confusion)

    evaluated = [r for r in results if r.error is None]
    excluded = [r for r in results if r.error is not None]

    behavior = _category_behavior_metrics(evaluated)
    grounding = _grounding_rate_metrics(grounding_traces)
    confidence_by_category = _confidence_by_category(evaluated)

    print("\n" + "=" * 78, flush=True)
    print("SUMMARY", flush=True)
    print("=" * 78, flush=True)
    if excluded:
        print(
            f"  EXCLUDED from metrics (harness/network error, not a routing result): "
            f"{len(excluded)} -- {[r.entry['id'] for r in excluded]}",
            flush=True,
        )
    print(f"  clear-answer questions   : {sum(1 for r in evaluated if r.is_positive)}", flush=True)
    print(f"  ambiguous/no-information : {sum(1 for r in evaluated if not r.is_positive)}", flush=True)
    print(
        f"  current default ({current_threshold}) -- precision={current_metrics['precision']}, "
        f"recall={current_metrics['recall']}, f1={current_metrics['f1']}",
        flush=True,
    )
    print(
        f"  best candidate ({best['threshold']}) -- precision={best['precision']}, "
        f"recall={best['recall']}, f1={best['f1']}",
        flush=True,
    )
    _print_confidence_distribution(confidence_by_category)
    _print_category_behavior(behavior)
    if not args.skip_grounding_trace:
        _print_grounding_rate(grounding)

    f1_margin = (best["f1"] or 0) - (current_metrics["f1"] or 0)
    if best["threshold"] == current_threshold:
        print(
            f"\n  The current default ({current_threshold}) already ties or beats every other "
            "candidate threshold on this dataset -- no change supported.",
            flush=True,
        )
    elif f1_margin < _MARGIN_FOR_CHANGE:
        print(
            f"\n  {best['threshold']} scores marginally higher than the current default "
            f"({current_threshold}) on this dataset (f1 {best['f1']:.3f} vs "
            f"{current_metrics['f1']:.3f}, a {f1_margin:.3f} margin on {len(evaluated)} "
            f"questions) -- too small a margin, and too small a dataset, to treat as evidence "
            "for a change. Re-run against a larger dataset before touching the default.",
            flush=True,
        )
    else:
        print(
            f"\n  {best['threshold']} outperforms the current default ({current_threshold}) on "
            f"this dataset (f1 {best['f1']} vs {current_metrics['f1']}). See "
            "app/shared/config/settings.py's confidence_threshold comment before changing the "
            "default off the back of a single run -- re-run against a larger/refreshed dataset "
            "first if this is the only evidence so far.",
            flush=True,
        )

    report = _build_report(
        org_name,
        args.org_slug,
        results,
        sweep_rows,
        best,
        current_threshold,
        behavior,
        grounding,
        grounding_traces,
        confidence_by_category,
    )
    args.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report written to: {args.report_path}", flush=True)

    if args.compare_to:
        _compare_reports(_load_report(args.compare_to), report)

    return report


def _parse_thresholds(raw: str | None) -> list[float]:
    if not raw:
        return list(_CANDIDATE_THRESHOLDS)
    return [float(part.strip()) for part in raw.split(",") if part.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate EKIP's confidence-gated routing against real ingested data."
    )
    parser.add_argument("--org-slug", default=_DEFAULT_ORG_SLUG, help="organization whose ingested data to query")
    parser.add_argument(
        "--category",
        choices=["clear-answer", "ambiguous", "no-information"],
        help="run only one category of question",
    )
    parser.add_argument("--limit", type=int, help="run at most N questions")
    parser.add_argument(
        "--thresholds",
        type=_parse_thresholds,
        default=None,
        help="comma-separated candidate thresholds to sweep (default: 0.40 through 0.80 in steps of 0.05)",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=_DEFAULT_REPORT_PATH,
        help="where to write the full JSON report",
    )
    parser.add_argument(
        "--compare-to",
        type=Path,
        default=None,
        help="path to a previously-written report JSON to diff this run against (before/after)",
    )
    parser.add_argument(
        "--skip-grounding-trace",
        action="store_true",
        help="skip the direct grounding-rate trace (faster, but no answer-grounding-rate metric)",
    )
    args = parser.parse_args()
    if args.thresholds is None:
        args.thresholds = list(_CANDIDATE_THRESHOLDS)
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
