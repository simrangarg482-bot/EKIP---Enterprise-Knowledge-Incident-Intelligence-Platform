"""Loads the evaluation harness's golden Q/A set (`GoldenCase`, see
`schemas.py`) from a JSON file.

Phase 1's own roadmap entry is explicit that "the hard part is authoring the
golden dataset, not the harness code" -- `golden_qa_set.json`, bundled
alongside this module, is a genuine starting set (12 questions spanning
every connector source this codebase ships: Slack, GitHub, Jira, Confluence,
SharePoint, Azure DevOps, Teams, and the internal runbooks connector, plus
one deliberately out-of-scope question to check the harness doesn't reward
confident hallucination on an unanswerable query), not a placeholder -- but
it is deliberately far short of the "100-200 hand-labeled pairs" the roadmap
describes as the real target. Authoring that larger set requires real,
ingested organization content to write expected answers/sources against,
which does not exist in this repository (confirmed: no seed script ingests
any documents at all, see `scripts/seed_test_organization.py`'s own scope) --
this bundled set is themed after the same illustrative "checkout service"
incident scenario `docs/USER_TESTING_GUIDE.md` already uses for manual
testing, so it is at least answerable against the kind of content that guide
walks a user through ingesting, not an arbitrary fictional company.

Same golden-set *schema* (`question` + `expected_sources`) `tests/
ingestion_retrieval/test_end_to_end_rag.py`'s own (never-committed, expected
to be hand-authored locally) `questions.json` already established --
matched here deliberately, not reinvented, so a question written for one
harness is trivially portable to the other.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.evaluation.schemas import GoldenCase

_DEFAULT_GOLDEN_SET_PATH = Path(__file__).resolve().parent / "golden_qa_set.json"


class GoldenSetError(Exception):
    """Raised when a golden-set file doesn't parse as JSON, isn't a JSON
    array, contains a malformed case, or has a duplicate `case_id` -- a
    harness run should fail loudly and early on a bad golden set, not
    silently skip or duplicate-count a case.
    """


def load_golden_set(path: Path | None = None) -> list[GoldenCase]:
    """Load and validate every `GoldenCase` from `path` (defaults to the
    bundled `golden_qa_set.json` next to this module).

    A separate, organization-specific golden set (once real content exists
    to write one against, per this module's own docstring) is loaded the
    same way -- just point `path` at it; `scripts/run_evaluation.py` exposes
    this as a `--golden-set` CLI argument.
    """
    target_path = path if path is not None else _DEFAULT_GOLDEN_SET_PATH

    try:
        raw_text = target_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GoldenSetError(f"Could not read golden set file {target_path}: {exc}") from exc

    try:
        raw_entries = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise GoldenSetError(f"Golden set file {target_path} is not valid JSON: {exc}") from exc

    if not isinstance(raw_entries, list):
        raise GoldenSetError(
            f"Golden set file {target_path} must contain a JSON array of cases, "
            f"got {type(raw_entries).__name__}"
        )

    cases: list[GoldenCase] = []
    seen_case_ids: set[str] = set()
    for index, raw_entry in enumerate(raw_entries):
        try:
            case = GoldenCase.model_validate(raw_entry)
        except ValidationError as exc:
            raise GoldenSetError(
                f"Golden set file {target_path}, entry {index}: {exc}"
            ) from exc

        if case.case_id in seen_case_ids:
            raise GoldenSetError(
                f"Golden set file {target_path} has a duplicate case_id: {case.case_id!r}"
            )
        seen_case_ids.add(case.case_id)
        cases.append(case)

    if not cases:
        raise GoldenSetError(f"Golden set file {target_path} contains zero cases.")

    return cases
