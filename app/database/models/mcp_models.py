"""SQLAlchemy model for `mcp_requests` (DATABASE_DESIGN.md "mcp/ -- owned
tables" -- conceptually, not literally: see below).

Owned by: database/ (definition) + core/observability (write access) --
**not** mcp/ itself, despite the table conceptually belonging to mcp/'s
concerns. `pyproject.toml`'s import-linter contract forbids `app.mcp` from
importing `app.database` in any form -- not just business tables, this
table too -- so mcp/ cannot hold its own repository.py here the way
`agent_models.py`'s docstring describes for agents/. `core.observability`
exists purely to give mcp/ a `core`-side function
(`core.observability.service.record_mcp_request`) to call instead; see that
module's own docstring for the full reasoning. This is a deliberate,
discussed deviation from DATABASE_DESIGN.md's table-ownership heading, not
an oversight -- confirmed against the enforced import-linter contract taking
precedence over the docstring's looser "owned by" framing.

Unlike `AgentExecution`, no `organization_id` column is added beyond
DATABASE_DESIGN.md's literal column list. `agent_executions` needed that
addition because a *named* downstream consumer (the Knowledge Gap Agent,
PROJECT_PLAN.md section 6.6) has to cluster low-confidence executions
per-tenant. `mcp_requests`' own documented purpose is narrower --
"MCP latency metrics" (DATABASE_DESIGN.md) via its `(tool_name,
occurred_at DESC)` index -- with no equivalent per-tenant consumer named
anywhere in the docs. Adding an unused column on spec would be inventing a
requirement rather than fixing a flagged gap; if a tenant-scoped MCP usage
report becomes a real, named need later, this is an easy additive column at
that point, the same way `agent_executions`' own addition was.

`identity` is the resolved caller's `Identity.audit_tag` string (e.g.
`"user:3f2a..."`), not a foreign key -- matching every other tagged-actor
column in this codebase (`incident_timeline.actor`, `postmortems.
generated_by`); an MCP request's caller can be a user or a service/agent
identity, and a plain string is what the rest of the schema already uses
for that human-vs-AI-vs-service distinction.

`request_summary` is JSONB for the same reason `agent_executions.
input_summary` is: a structured summary for observability, not the raw
tool-call payload, to avoid indefinitely storing potentially sensitive full
request bodies.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base


class McpRequest(Base):
    """One logged MCP tool call -- observability record backing the MCP
    latency metrics referenced throughout PROJECT_PLAN.md/DATABASE_DESIGN.md.

    Unlike `AgentExecution`, this is not a running->succeeded/failed
    lifecycle row: a tool call's outcome, latency, and status are all known
    by the time anything is written, so `mcp.repository.insert_mcp_request`
    performs exactly one insert per call, after it completes (successfully
    or not) -- there is no "running" state to record separately.
    """

    __tablename__ = "mcp_requests"
    __table_args__ = (Index("ix_mcp_requests_tool_name_occurred_at", "tool_name", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    identity: Mapped[str] = mapped_column(Text, nullable=False)
    request_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OAuthClient(Base):
    """A dynamically-registered MCP OAuth client (RFC 7591), persisted so a
    server restart does not orphan a remote MCP client's (e.g. Claude's)
    long-lived `client_id`/`client_secret`/refresh token -- see
    `app.mcp.oauth.provider`'s module docstring for the gap this closes: that
    provider used to hold registered clients in a plain in-memory `dict`,
    which every server restart during this project's own Claude-integration
    testing emptied out from under an already-connected client.

    Deliberately NOT organization-scoped and NOT RLS-protected -- the same
    reasoning `Role`/`Permission` (core_models.py) already establish for a
    platform-wide catalog: an OAuth client (Claude itself, as a piece of
    software) is not a member of any one organization. Which organization a
    given authorization grant is *for* is decided per `/authorize` call
    (`EkipOAuthProvider.authorize`'s human confirmation step), not by which
    client registered -- the resulting session (a real `refresh_tokens` row)
    remains exactly as organization-scoped and RLS-protected as any other.

    `client_metadata` is the full RFC 7591 client metadata (everything
    `OAuthClientInformationFull` carries except `client_id`/`client_secret`/
    `client_id_issued_at`/`client_secret_expires_at`, which get their own
    columns) stored as one JSONB blob rather than exploded column-by-column
    -- the same choice `mcp_requests.request_summary` makes for a
    self-contained, SDK-owned shape this table never needs to query into.

    `client_secret_encrypted` is envelope-encrypted (`app.shared.security.
    envelope`), not hashed: unlike a password, `mcp.server.auth.middleware.
    client_auth.ClientAuthenticator` (the `mcp` package's own `/token`
    client-authentication check) compares the *plaintext* secret via
    `hmac.compare_digest` against whatever this table's caller returns from
    `get_client()` -- a one-way hash cannot support that comparison, so
    reversible encryption (the same mechanism `connector_configs.
    credential_ref` already uses for an identical "must get the plaintext
    back" requirement) is the correct choice, not a shortcut.
    """

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(Text, primary_key=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_secret_expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_id_issued_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    client_metadata: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
