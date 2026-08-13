"""add password auth and agent_executions.user_id

Revision ID: c8f1a4d7e2b3
Revises: b4c7e2a9f5d1
Create Date: 2026-08-12 00:00:00.000000

Two independent, additive changes bundled in one migration:

1. `users.password_hash` (nullable) -- backs `core.auth.service.signup`/
   `login_with_password`, a new email+password auth path that runs alongside
   the existing SSO/OIDC flow, not in place of it. `NULL` for every
   SSO-provisioned account.
2. `agent_executions.user_id` (nullable FK to `users.id`, `ON DELETE SET
   NULL`) -- lets a REST-triggered question/investigation be attributed to
   the human who asked it, backing a new "conversation history" read
   endpoint (`GET /ask/history`). `NULL` for MCP/scheduled executions, which
   have no human user to attribute to.

Neither column is part of any RLS policy predicate (both are per-row
attributes, not tenant boundaries), so no policy changes are needed --
`agent_executions`'s existing organization_id-based RLS policy already
covers rows with this new column.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c8f1a4d7e2b3'
down_revision: str | None = 'b4c7e2a9f5d1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('users', sa.Column('password_hash', sa.Text(), nullable=True))

    op.add_column('agent_executions', sa.Column('user_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'fk_agent_executions_user_id_users',
        'agent_executions', 'users',
        ['user_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_agent_executions_user_started_at',
        'agent_executions',
        ['user_id', 'started_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_agent_executions_user_started_at', table_name='agent_executions')
    op.drop_constraint('fk_agent_executions_user_id_users', 'agent_executions', type_='foreignkey')
    op.drop_column('agent_executions', 'user_id')

    op.drop_column('users', 'password_hash')
