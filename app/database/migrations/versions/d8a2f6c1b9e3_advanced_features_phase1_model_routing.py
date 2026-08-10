"""Advanced Features Roadmap Phase 1: model routing (2.4)

Revision ID: d8a2f6c1b9e3
Revises: d4f7b2e9c6a3
Create Date: 2026-08-09 00:00:00.000000

Adds `model_used`/`prompt_tokens`/`completion_tokens`/`total_tokens` onto the
pre-existing `agent_executions` table -- the roadmap's own text for this
item: "log model_used + token counts onto agent_executions (the table
already exists, just add columns)". See `app.database.models.agent_models.
AgentExecution`'s own docstring for why all four are nullable and why
`model_used` is a joined list rather than one value.

No RLS changes here: `agent_executions` already has its own tenant-isolation
policy from an earlier migration (Milestone 10) -- adding nullable columns
to an already-RLS-enabled table needs no policy change, since Postgres RLS
policies are row-scoped (by `organization_id`), not column-scoped.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd8a2f6c1b9e3'
down_revision: str | None = 'd4f7b2e9c6a3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('agent_executions', sa.Column('model_used', sa.Text(), nullable=True))
    op.add_column('agent_executions', sa.Column('prompt_tokens', sa.Integer(), nullable=True))
    op.add_column('agent_executions', sa.Column('completion_tokens', sa.Integer(), nullable=True))
    op.add_column('agent_executions', sa.Column('total_tokens', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('agent_executions', 'total_tokens')
    op.drop_column('agent_executions', 'completion_tokens')
    op.drop_column('agent_executions', 'prompt_tokens')
    op.drop_column('agent_executions', 'model_used')
