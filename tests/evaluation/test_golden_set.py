"""Unit tests for `app.evaluation.golden_set`: the loader/validator, and a
sanity check on the bundled starter dataset itself.
"""

from __future__ import annotations

import json

import pytest

from app.evaluation.golden_set import GoldenSetError, load_golden_set
from app.evaluation.schemas import GoldenCase


def _write(tmp_path, content: str):
    path = tmp_path / "questions.json"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_golden_set_defaults_to_the_bundled_starter_set() -> None:
    cases = load_golden_set()

    assert len(cases) > 0
    assert all(isinstance(case, GoldenCase) for case in cases)
    case_ids = [case.case_id for case in cases]
    assert len(case_ids) == len(set(case_ids)), "bundled starter set must have unique case_ids"
    assert all(case.question.strip() for case in cases)


def test_load_golden_set_covers_every_shipped_connector_source() -> None:
    """The bundled starter set is documented (golden_set.py's module
    docstring) as spanning every connector source this codebase ships --
    regression-guard that claim so it can't silently drift as the set is
    edited.
    """
    cases = load_golden_set()
    all_sources = {source for case in cases for source in case.expected_sources}

    for expected in {
        "slack", "github", "jira", "confluence", "sharepoint", "azure_devops", "teams", "runbooks",
    }:
        assert expected in all_sources, f"golden set is missing an example for source={expected!r}"


def test_load_golden_set_from_explicit_path(tmp_path) -> None:
    path = _write(
        tmp_path,
        json.dumps([{"case_id": "c1", "question": "q1", "expected_sources": ["github"]}]),
    )

    cases = load_golden_set(path)

    assert len(cases) == 1
    assert cases[0].case_id == "c1"
    assert cases[0].expected_sources == ["github"]
    assert cases[0].notes is None


def test_load_golden_set_rejects_non_json(tmp_path) -> None:
    path = _write(tmp_path, "not json at all")
    with pytest.raises(GoldenSetError, match="not valid JSON"):
        load_golden_set(path)


def test_load_golden_set_rejects_non_array_top_level(tmp_path) -> None:
    path = _write(tmp_path, json.dumps({"case_id": "c1", "question": "q1"}))
    with pytest.raises(GoldenSetError, match="JSON array"):
        load_golden_set(path)


def test_load_golden_set_rejects_malformed_entry(tmp_path) -> None:
    path = _write(tmp_path, json.dumps([{"case_id": "c1"}]))  # missing required "question"
    with pytest.raises(GoldenSetError):
        load_golden_set(path)


def test_load_golden_set_rejects_duplicate_case_ids(tmp_path) -> None:
    path = _write(
        tmp_path,
        json.dumps(
            [
                {"case_id": "dup", "question": "q1"},
                {"case_id": "dup", "question": "q2"},
            ]
        ),
    )
    with pytest.raises(GoldenSetError, match="duplicate case_id"):
        load_golden_set(path)


def test_load_golden_set_rejects_empty_array(tmp_path) -> None:
    path = _write(tmp_path, json.dumps([]))
    with pytest.raises(GoldenSetError, match="zero cases"):
        load_golden_set(path)


def test_load_golden_set_rejects_missing_file(tmp_path) -> None:
    with pytest.raises(GoldenSetError, match="Could not read"):
        load_golden_set(tmp_path / "does_not_exist.json")
