"""add repo_full_name to code_chunks

Revision ID: d1a2b3c4e5f6
Revises: c8f1a4d7e2b3
Create Date: 2026-08-12 00:00:00.000000

Backs a new `repository` filter on `retrieval.search()`/`lexical_search()`
(`SearchFilters.repository`): a denormalized, indexed column on
`code_chunks` only -- not the shared `_ChunkColumns` mixin -- since
"repository" is a GitHub-connector-specific concept with no meaning for
`documentation_chunks`/`conversations_chunks`. Populated at upsert time from
`UpsertChunk.repo_full_name`, itself sourced from the GitHub connector's
existing `document_metadata` `repo` entry; every other connector leaves it
`NULL`.

Not part of any RLS policy predicate -- the existing `organization_id`-based
tenant_isolation policy on `code_chunks` already covers rows with this new
column, so no policy changes are needed.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd1a2b3c4e5f6'
down_revision: str | None = 'c8f1a4d7e2b3'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('code_chunks', sa.Column('repo_full_name', sa.Text(), nullable=True))
    op.create_index(
        'ix_code_chunks_org_repo_full_name',
        'code_chunks',
        ['organization_id', 'repo_full_name'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_code_chunks_org_repo_full_name', table_name='code_chunks')
    op.drop_column('code_chunks', 'repo_full_name')
