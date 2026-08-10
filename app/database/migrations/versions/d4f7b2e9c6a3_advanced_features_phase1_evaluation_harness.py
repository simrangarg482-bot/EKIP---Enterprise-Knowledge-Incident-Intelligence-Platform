"""Advanced Features Roadmap Phase 1: evaluation harness (2.2)

Revision ID: d4f7b2e9c6a3
Revises: b6e9c2a4f7d1
Create Date: 2026-08-08 00:00:00.000000

Creates `eval_runs`/`eval_case_results` -- `app.database.models.
evaluation_models.EvalRun`/`EvalCaseResult` have existed in this repo since
an earlier pass (already imported into `app.database.migrations.base` for
autogenerate), but no migration ever actually ran `CREATE TABLE` for either
-- the exact same gap `e3f6a1b8d4c9_add_mcp_requests_table.py` closed for
`mcp_requests`. This migration closes it for the evaluation harness.

Both tables carry a direct `organization_id` column (see
`evaluation_models.py`'s own docstring for why: "the same choice
`IncidentTimeline`/`Postmortem` make ... a direct column lets a Postgres RLS
policy check `organization_id` in place, without a subquery back to the
parent table"), so -- unlike `mcp_requests`, which was deliberately excluded
from RLS for carrying no `organization_id` at all -- both tables get the
identical `ENABLE/FORCE ROW LEVEL SECURITY` + `tenant_isolation` policy every
other direct-`organization_id` table already has (see
`c7d4e8f19a2b_milestone_10_row_level_security.py`'s `_DIRECT_TABLES` list and
module docstring for the exact design this reuses verbatim, GUC name
included). Leaving a new tenant-scoped table without this backstop would be
a regression against that migration's own stated policy, not just an
omission.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd4f7b2e9c6a3'
down_revision: str | None = 'b6e9c2a4f7d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same GUC/policy name the milestone 10 RLS migration established -- reused
# verbatim, not redefined, so both migrations are provably checking the same
# session variable.
_GUC_NAME = 'app.current_organization_id'
_POLICY_NAME = 'tenant_isolation'
_RLS_TABLES = ['eval_runs', 'eval_case_results']


def _direct_using_clause() -> str:
    return f"organization_id = current_setting('{_GUC_NAME}', true)::uuid"


def upgrade() -> None:
    op.create_table(
        'eval_runs',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('model_used', sa.Text(), nullable=False),
        sa.Column('git_commit', sa.Text(), nullable=True),
        sa.Column('case_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('passed_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('hallucination_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('avg_relevance_score', sa.Numeric(), nullable=True),
        sa.Column('avg_citation_accuracy_score', sa.Numeric(), nullable=True),
        sa.Column('avg_confidence_score', sa.Numeric(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False, server_default='running'),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column(
            'started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_eval_runs_org_started_at', 'eval_runs', ['organization_id', 'started_at'],
        unique=False,
    )

    op.create_table(
        'eval_case_results',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('eval_run_id', sa.UUID(), nullable=False),
        sa.Column('case_id', sa.Text(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('route_taken', sa.Text(), nullable=True),
        sa.Column('confidence_score', sa.Numeric(), nullable=True),
        sa.Column('citation_count', sa.Integer(), nullable=False, server_default='0'),
        # No `server_default` on either JSONB column -- matching
        # `a1c3e9f2b7d4_milestone_9_knowledge_gap_reports.py`'s identical
        # choice for its own JSONB columns: the ORM's `default=list`
        # (`evaluation_models.py`) supplies `[]` at insert time, and every
        # real writer (`app.evaluation.repository.insert_eval_case_result`)
        # always passes both explicitly. A server-side default would need a
        # `::jsonb` cast on the literal to be valid DDL; omitting it entirely
        # avoids that footgun rather than risking it.
        sa.Column('expected_sources', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('actual_sources', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('relevance_score', sa.Integer(), nullable=True),
        sa.Column('citation_accuracy_score', sa.Integer(), nullable=True),
        sa.Column('completeness_score', sa.Integer(), nullable=True),
        sa.Column('grounded', sa.Boolean(), nullable=True),
        sa.Column('hallucination_flag', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('passed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('judge_reasoning', sa.Text(), nullable=True),
        sa.Column('error_detail', sa.Text(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['eval_run_id'], ['eval_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_eval_case_results_run_id', 'eval_case_results', ['eval_run_id'], unique=False,
    )
    op.create_index(
        'ix_eval_case_results_org_id', 'eval_case_results', ['organization_id'], unique=False,
    )

    for table in _RLS_TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(
            f'CREATE POLICY {_POLICY_NAME} ON {table} USING ({_direct_using_clause()})'
        )


def downgrade() -> None:
    for table in _RLS_TABLES:
        op.execute(f'DROP POLICY IF EXISTS {_POLICY_NAME} ON {table}')
        op.execute(f'ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY')

    op.drop_index('ix_eval_case_results_org_id', table_name='eval_case_results')
    op.drop_index('ix_eval_case_results_run_id', table_name='eval_case_results')
    op.drop_table('eval_case_results')

    op.drop_index('ix_eval_runs_org_started_at', table_name='eval_runs')
    op.drop_table('eval_runs')
