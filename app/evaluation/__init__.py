"""evaluation/ -- the golden Q/A evaluation harness (Advanced Features
Roadmap Phase 1, "Evaluation harness (2.2)": "golden Q/A set + LLM-judge
scoring script + trend storage").

Owned by: evaluation/. Answers the question nothing else in EKIP could
answer before this existed: does the confidence score, the grounding check,
and the retrieval quality actually work, measured against a real,
hand-authored set of questions with known-good expected sources -- not just
asserted.

See `runner.py` for the actual orchestration (`run_evaluation`: golden case
-> real `answer_question` call -> LLM-judge scoring -> persisted
`eval_runs`/`eval_case_results` row), `judge.py` for the scoring rubric,
`golden_set.py` for the question format + bundled starter dataset, and
`repository.py` for the two tables this module owns
(`app.database.models.evaluation_models.EvalRun`/`EvalCaseResult`).

`scripts/run_evaluation.py` is the CLI entrypoint a human (or a future CI
step, Phase 1's own "CI pipeline" item) actually invokes.
"""

from __future__ import annotations
