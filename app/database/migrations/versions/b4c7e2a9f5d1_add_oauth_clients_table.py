"""add oauth_clients table

Revision ID: b4c7e2a9f5d1
Revises: e3f6a1b8d4c9
Create Date: 2026-08-12 00:00:00.000000

`app.mcp.oauth.provider.EkipOAuthProvider` previously stored every
dynamically-registered OAuth client (RFC 7591) in a plain in-memory `dict`.
Every MCP server restart during this project's own Claude remote-connector
testing wiped that dict, orphaning Claude's already-cached `client_id`/
`client_secret`/refresh token and forcing a fresh registration + re-
authorization every time the server had to restart. This table gives
client registrations the same restart-survival every other long-lived
credential in this schema already has.

Not RLS-protected, matching `roles`/`permissions` (platform-wide catalogs,
not per-organization data) -- see `app.database.models.mcp_models.
OAuthClient`'s own docstring for the full reasoning.
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b4c7e2a9f5d1'
down_revision: str | None = 'e3f6a1b8d4c9'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'oauth_clients',
        sa.Column('client_id', sa.Text(), nullable=False),
        sa.Column('client_secret_encrypted', sa.Text(), nullable=True),
        sa.Column('client_secret_expires_at', sa.Integer(), nullable=True),
        sa.Column('client_id_issued_at', sa.Integer(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('client_id'),
    )


def downgrade() -> None:
    op.drop_table('oauth_clients')
