"""CLI entrypoint for the evaluation harness (Advanced Features Roadmap
Phase 1, "Evaluation harness (2.2)") -- runs the golden Q/A set against a
real organization's real ingested content, scores each answer with an
LLM-judge, and prints a run summary plus the last few runs' trend.

Bootstrap style matches `scripts/seed_test_organization.py`: `configure_
logging()`, a thin `async def main()` opened with `session_scope()`, run via
`asyncio.run`. Unlike that script, this one does not create an organization
-- it requires one that already exists and already has real ingested content
to answer the golden set's questions against (an eval run against an empty
knowledge base will legitimately score everything as ungrounded/low-
confidence, which is a correct result, not a bug in this script).

Run (against the test org `seed_test_organization.py` creates, once it has
had something real ingested into it):
    python scripts/run_evaluation.py --organization-id <uuid>

Optional:
    --golden-set /path/to/questions.json   (defaults to the bundled starter set)
    --git-commit <sha>                     (recorded on the eval_runs row, e.g. `$(git rev-parse HEAD)`)
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path

from app.database.session import session_scope, set_tenant_context
from app.evaluation.golden_set import GoldenSetError, load_golden_set
from app.evaluation.repository import list_recent_eval_runs
from app.evaluation.runner import run_evaluation
from app.evaluation.schemas import EvalRunSummary
from app.shared.config.logging import configure_logging
from app.shared.config.tracing import configure_tracing
from app.shared.schemas import Identity

configure_logging()
configure_tracing()

_AGENT_NAME = "evaluation_harness"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--organization-id",
        required=True,
        type=uuid.UUID,
        help="Organization to run the golden set against (must already have ingested content).",
    )
    parser.add_argument(
        "--golden-set",
        type=Path,
        default=None,
        help="Path to a golden-set JSON file. Defaults to the bundled starter set "
        "(app/evaluation/golden_qa_set.json).",
    )
    parser.add_argument(
        "--git-commit",
        default=None,
        help="Git commit SHA to record on this run, for correlating eval trend with code changes.",
    )
    return parser.parse_args()


def _print_summary(summary: EvalRunSummary) -> None:
    print(f"\n--- Evaluation run {summary.eval_run_id} ({summary.status}) ---")
    print(f"Model used:  {summary.model_used}")
    print(f"Git commit:  {summary.git_commit or '(none)'}")
    print(f"Cases:       {summary.passed_count}/{summary.case_count} passed")
    print(f"Hallucinations flagged: {summary.hallucination_count}/{summary.case_count}")
    print(f"Avg relevance score:         {_fmt(summary.avg_relevance_score)}")
    print(f"Avg citation accuracy score: {_fmt(summary.avg_citation_accuracy_score)}")
    print(f"Avg confidence score:        {_fmt(summary.avg_confidence_score)}")

    print("\nPer-case results:")
    for case in summary.cases:
        status = "PASS" if case.passed else "FAIL"
        print(
            f"  [{status}] {case.case_id}: route={case.route_taken or '?'} "
            f"confidence={_fmt(case.confidence_score)} "
            f"relevance={case.relevance_score if case.relevance_score is not None else '?'} "
            f"grounded={case.grounded} hallucination={case.hallucination_flag}"
        )
        if case.error_detail:
            print(f"      error: {case.error_detail}")


def _fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "n/a"


async def main() -> None:
    args = _parse_args()

    try:
        golden_cases = load_golden_set(args.golden_set)
    except GoldenSetError as exc:
        raise SystemExit(f"Could not load golden set: {exc}") from exc

    print(f"Loaded {len(golden_cases)} golden case(s).")

    async with session_scope() as session:
        await set_tenant_context(session, args.organization_id)
        actor = Identity.for_agent(_AGENT_NAME, args.organization_id)

        summary = await run_evaluation(
            session, actor, golden_cases, git_commit=args.git_commit
        )

        recent_runs = await list_recent_eval_runs(session, args.organization_id, limit=5)

    _print_summary(summary)

    print("\n--- Recent runs (trend) ---")
    for run in recent_runs:
        pass_rate = f"{run.passed_count}/{run.case_count}" if run.case_count else "n/a"
        print(
            f"  {run.started_at.isoformat()}  status={run.status}  passed={pass_rate}  "
            f"commit={run.git_commit or '(none)'}"
        )


if __name__ == "__main__":
    asyncio.run(main())
