"""add repo_full_name to documentation_chunks

Revision ID: e2b3c4d5f6a7
Revises: d1a2b3c4e5f6
Create Date: 2026-08-12 00:10:00.000000

Follow-up to `d1a2b3c4e5f6` (which added `repo_full_name` to `code_chunks`
only): verifying real ingestion of `mehulagarwal13/test-1` end to end showed
that ingestion's content-type classification routes most actual GitHub
connector output -- issues, PR descriptions, commit messages, READMEs -- to
`ContentType == "document"` (the `"documentation"` collection), not
`"code"`. A repo with no source files at all (this one has only a README)
produces zero `code_chunks` rows, making a `code_chunks`-only repository
filter unusable for exactly the data GitHub ingestion typically produces.
`conversations_chunks` (Slack/Teams) is still excluded -- nothing ever
attaches a GitHub repo to a chat chunk.

Not part of any RLS policy predicate, same reasoning as `d1a2b3c4e5f6`.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e2b3c4d5f6a7'
down_revision: str | None = 'd1a2b3c4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('documentation_chunks', sa.Column('repo_full_name', sa.Text(), nullable=True))
    op.create_index(
        'ix_documentation_chunks_org_repo_full_name',
        'documentation_chunks',
        ['organization_id', 'repo_full_name'],
        unique=False,
    )
    # Backfill existing rows from `document_metadata`'s already-correct
    # GitHub-connector `repo` entry -- denormalizing data that already
    # exists, not inventing anything. Also covers `code_chunks` rows
    # inserted between `d1a2b3c4e5f6` and this migration (none existed yet
    # in practice, but the backfill is safe/idempotent either way).
    op.execute(
        """
        UPDATE documentation_chunks dc
        SET repo_full_name = dm.value
        FROM document_metadata dm
        WHERE dm.document_id = dc.document_id
          AND dm.key = 'repo'
          AND dc.repo_full_name IS NULL
        """
    )
    op.execute(
        """
        UPDATE code_chunks cc
        SET repo_full_name = dm.value
        FROM document_metadata dm
        WHERE dm.document_id = cc.document_id
          AND dm.key = 'repo'
          AND cc.repo_full_name IS NULL
        """
    )


def downgrade() -> None:
    op.drop_index('ix_documentation_chunks_org_repo_full_name', table_name='documentation_chunks')
    op.drop_column('documentation_chunks', 'repo_full_name')
