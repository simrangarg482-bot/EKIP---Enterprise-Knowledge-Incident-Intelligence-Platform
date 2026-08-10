"""SQLAlchemy models for the tables owned by evaluation/ -- `eval_runs` and
`eval_case_results` (Advanced Features Roadmap Phase 1, "Evaluation harness
(2.2)": "golden Q/A set + LLM-judge scoring script + trend storage").

Owned by: database/ (definition) + evaluation/ (write access) -- same
ownership discipline as `agent_models.py`'s `AgentExecution`/
`KnowledgeGapReport`: only `app.evaluation.repository` writes here.

Two tables, mirroring `AgentExecution`'s own "one row per run" shape one
level deeper:
  - `EvalRun` -- one row per harness execution (a `running` ->
    `succeeded`/`failed` lifecycle, identical to `AgentExecution`'s), holding
    the run-level aggregate scores. This is the "trend storage" the roadmap
    calls for: querying `eval_runs` ordered by `started_at` is how a future
    caller (a CI gate in Phase 1's own "CI pipeline" item, or a dashboard)
    sees whether eval quality is improving or regressing release over
    release.
  - `EvalCaseResult` -- one row per golden-set question *within* one run,
    holding that question's own judge scores. Kept as a child table rather
    than folding everything into `EvalRun` as a JSON blob, for the same
    reason `IncidentTimeline` rows aren't folded into `Incident`: per-case
    results are independently queryable (e.g. "show me every run where
    question X regressed") and a fixed-shape row is easier to aggregate over
    in SQL than reaching into a JSON array every time.

`organization_id` is carried directly on *both* tables (not just derived via
a join from `EvalCaseResult.eval_run_id` -> `EvalRun.organization_id`) --
the same choice `IncidentTimeline`/`Postmortem` make over
`document_metadata`/`project_memberships`'s join-scoped alternative: a
direct column lets a Postgres RLS policy check `organization_id` in place,
without a subquery back to the parent table (see `IncidentTimeline`'s own
docstring for this exact reasoning, reused here rather than re-derived).

Every score column is nullable: a case can fail before the judge ever runs
(e.g. `answer_question` itself raised for that question), in which case the
row still gets written -- `passed=False`, every score `NULL` -- so a failed
case is visible in the trend data, not silently absent from it.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class EvalRun(Base):
    """One execution of the evaluation harness (`app.evaluation.runner.
    run_evaluation`) against the golden Q/A set.

    `status`/`error_detail`/`started_at`/`completed_at` mirror
    `AgentExecution`'s identical running->succeeded/failed lifecycle
    columns -- a harness run is itself an agent-adjacent execution worth
    observing the same way, not a special case needing its own vocabulary.
    """

    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("ix_eval_runs_org_started_at", "organization_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    model_used: Mapped[str] = mapped_column(Text, nullable=False)
    git_commit: Mapped[str | None] = mapped_column(Text, nullable=True)
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hallucination_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_relevance_score: Mapped[float | None] = mapped_column(
        Numeric(asdecimal=False), nullable=True
    )
    avg_citation_accuracy_score: Mapped[float | None] = mapped_column(
        Numeric(asdecimal=False), nullable=True
    )
    avg_confidence_score: Mapped[float | None] = mapped_column(
        Numeric(asdecimal=False), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvalCaseResult(Base):
    """One golden-set question's outcome within one `EvalRun`.

    `expected_sources`/`actual_sources` are JSONB string lists, not a
    normalized join table -- the same "small, human-authored, not a scale
    concern" reasoning `KnowledgeGapReport.supporting_execution_ids` already
    applies; there is no query anywhere that needs to search *across* these
    lists, only display them alongside one case result.
    """

    __tablename__ = "eval_case_results"
    __table_args__ = (
        Index("ix_eval_case_results_run_id", "eval_run_id"),
        Index("ix_eval_case_results_org_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    eval_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    route_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(
        Numeric(asdecimal=False), nullable=True
    )
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    actual_sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    relevance_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation_accuracy_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completeness_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grounded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    hallucination_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    judge_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
