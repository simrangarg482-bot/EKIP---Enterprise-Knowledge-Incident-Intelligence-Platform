"""SQLAlchemy models for the tables owned by agents/: `agent_executions` and,
as of Milestone 9, `knowledge_gap_reports` (DATABASE_DESIGN.md "agents/ --
owned tables").

Owned by: database/ (definition) + agents/ (write access) -- same ownership
discipline as every other models file in this project: only agents/'s
repository code writes here; every other module reads agent results through
`agents`' public interface (`answer_question`, `triage_incident`,
`generate_postmortem`, `detect_knowledge_gaps`, PROJECT_PLAN.md section 9.7),
never by importing this model directly.

`organization_id` added beyond DATABASE_DESIGN.md's original column list --
that table definition predates the org-scoping migration (ENGINEERING_DECISIONS.md
#004) and was never revisited, the same gap `audit_logs` and `ingestion_jobs`
had before their own equivalent additions. Without it, the Knowledge Gap
Agent's own query ("recent low-confidence `agent_executions` rows",
PROJECT_PLAN.md section 6.6 / DATABASE_DESIGN.md's own "why this table
matters" note) would have no way to scope its clustering pass to one
organization at a time, silently mixing every tenant's low-confidence queries
together -- the same class of cross-tenant leak #004 fixed for role
resolution. Carried directly on the row (not derived solely via a join),
matching `IncidentTimeline`/`Postmortem`/`IngestionJob`'s convention.

`input_summary` is deliberately JSONB, not the raw prompt/context: per
DATABASE_DESIGN.md, "not the full prompt -- a structured summary for
observability, to avoid storing sensitive full context indefinitely" -- this
table is an execution-outcome log, not a prompt cache.

`confidence_score` uses `Numeric(asdecimal=False)` so SQLAlchemy hands back a
plain Python `float` (matching every other confidence-adjacent value in this
codebase -- `Settings.confidence_threshold`, `ScoredChunk.score` -- rather
than a `decimal.Decimal` the rest of the app would need to convert at every
read site).

`model_used`/`prompt_tokens`/`completion_tokens`/`total_tokens` added by the
Advanced Features Roadmap Phase 1 "Model routing (2.4)" feature
(`d8a2f6c1b9e3_advanced_features_phase1_model_routing.py`) -- the roadmap's
own text for this item: "log model_used + token counts onto
agent_executions (the table already exists, just add columns)". All four
are nullable: a still-`running` row, or a run whose graph raised before any
LLM call executed, legitimately has none of this data yet (or ever).
`model_used` is a comma-joined, sorted list of every *distinct* model
actually used across the run (`app.agents.llm.get_tracked_usage`), not a
single value -- one `answer_question` run may genuinely route several
different tasks (query rewrite, generation, grounding-check) to different
models, and collapsing that to one string would misrepresent which model(s)
did the work.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class AgentExecution(Base):
    """One run of one agent node/graph -- observability record, and the
    Knowledge Gap Agent's own data source (DATABASE_DESIGN.md: "repeated
    low-confidence_score executions on similar input_summary topics are the
    signal that drives documentation-gap recommendations").

    Not an audit-trail table (`core/audit`'s `audit_logs` already covers
    "who did what, when" for human-auditable actions) -- this is
    agents-internal execution telemetry: which agent ran, how it was
    triggered, how confident it was, and whether it succeeded.
    """

    __tablename__ = "agent_executions"
    __table_args__ = (
        Index("ix_agent_executions_agent_name_started_at", "agent_name", "started_at"),
        Index("ix_agent_executions_org_started_at", "organization_id", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    agent_name: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_source: Mapped[str] = mapped_column(Text, nullable=False)  # mcp/core_api/scheduled
    input_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(
        Numeric(asdecimal=False), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="running")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # --- Model routing (Advanced Features Roadmap Phase 1, "Model routing
    # (2.4)") -- see module docstring above.
    model_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)


class KnowledgeGapReport(Base):
    """One recommendation produced by the Knowledge Gap Agent (Milestone 9,
    AGENT_WORKFLOWS.md section 2.6 / PROJECT_PLAN.md section 6.6) --
    "suggested topic, supporting execution IDs, suggested action."

    Owned by: database/ (definition) + agents/ (write access) -- same
    convention as `AgentExecution` above; `core/knowledge` reads
    `related_document_id` indirectly (by id) when a human acts on a report,
    never by joining into this table directly.

    `topic_embedding` and `supporting_execution_ids` are JSONB, not a native
    `pgvector` column or a join table: the per-organization row count here
    is small (a handful of open gaps at a time, not millions), so the one
    place this embedding is ever compared against another
    (`app.agents.knowledge_gap.pipeline`'s re-run merge check) does the
    cosine-similarity comparison in Python against the handful of currently
    "open" rows, rather than needing a SQL-side vector index built for a
    scale this table will never reach.

    `status` (`"open"`/`"dismissed"`) is an addition beyond
    AGENT_WORKFLOWS.md's literal spec, flagged plainly rather than silently
    added: without some way to close a report, `GET /knowledge/gaps` would
    show the same recommendation forever, even after a human has already
    acted on it (created the runbook, updated the document) -- a real
    usability gap the original spec didn't address. Nothing currently sets
    `"dismissed"` (no dismiss action is wired up yet); the column exists so
    that action is additive, not a later breaking schema change.
    """

    __tablename__ = "knowledge_gap_reports"
    __table_args__ = (
        Index("ix_knowledge_gap_reports_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    suggested_topic: Mapped[str] = mapped_column(Text, nullable=False)
    topic_embedding: Mapped[list] = mapped_column(JSONB, nullable=False)
    supporting_execution_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    suggested_action: Mapped[str] = mapped_column(Text, nullable=False)  # new_runbook/update_existing
    related_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
