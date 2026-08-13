"""The EKIP MCP server instance (PROJECT_PLAN.md section 9.6 / section 7.2).

One `MCPServer` instance (the `mcp` package's real 2.x class name -- see
below), targeting the **streamable-HTTP** transport -- a hosted,
multi-tenant endpoint serving every organization's MCP traffic through one
running process, not a per-user local stdio subprocess. This was a
genuinely undecided choice in the docs (section 7.2's diagram lists "stdio
or HTTP+SSE" without picking one); streamable-HTTP was chosen because it
fits EKIP's actual deployment model -- many organizations, many users, one
hosted service, each request carrying its own bearer token -- the same
reasoning `mcp.auth`'s module docstring gives for resolving `Identity` per
call rather than once per long-lived connection.

Tool/resource/prompt handlers live in `mcp/tools/` and `mcp/resources/`,
each importing `mcp_server` from this module to register themselves via
`@mcp_server.tool()` / `@mcp_server.resource(...)` / `@mcp_server.prompt()`.
This module owns only the server instance and the bearer-token extraction
glue -- not any tool's business logic (ARCHITECTURE.md section 6: "no
business logic" in mcp/, enforced in practice by `mcp.dispatch.run_mcp_tool`,
which every handler calls into).

**`session_factory` -- why it lives here, as an injected, initially-`None`
attribute:** `pyproject.toml`'s import-linter contract forbids `app.mcp`
from importing `app.database` in any form (confirmed as the intended,
literal reading, not just a loose docstring aspiration -- see
`app.core.observability`'s module docstring), which means nothing under
`app.mcp` -- including this module and `app.mcp.dispatch` -- may import
`app.database.session.session_scope` to open its own DB session, even
though every `core`/`agents` function `mcp.dispatch.run_mcp_tool` calls into
needs one passed in. The fix is dependency inversion: this module declares
the *shape* it needs (a zero-argument callable returning an async context
manager yielding an `AsyncSession` -- `AsyncSession` is a third-party
SQLAlchemy type, not `app.database`, so typing against it here is fine)
without importing a concrete implementation. The actual process entrypoint,
`scripts/run_mcp_server.py` -- which lives *outside* `app.mcp` and is
therefore free to import `app.database.session` -- sets
`server.session_factory = session_scope` once at startup, before serving
any request. `app.mcp.dispatch.run_mcp_tool` reads this module's
`session_factory` attribute at call time (not via a top-level `from ...
import session_factory`, which would freeze on the pre-startup `None`).

**`set_tenant_context` -- same dependency-inversion trick, for Milestone
10's RLS backstop.** `app.mcp.dispatch.run_mcp_tool` resolves `Identity`
(and therefore `organization_id`) itself, and every RLS-protected table
query that follows needs `app.database.session.set_tenant_context` called
on that same session first -- but `app.mcp` still cannot import
`app.database` to call it directly. Declared here as another injected,
initially-`None` callable of a third-party-only shape (`AsyncSession`,
`uuid.UUID` -> `None`), set alongside `session_factory` by
`scripts/run_mcp_server.py` to the real `app.database.session.
set_tenant_context`, and read by `run_mcp_tool` at call time the same way.

**OAuth (2026-08-12 addition, see `app.mcp.oauth.provider`'s module docstring
for the full why):** Claude's "Add custom connector" UI requires either OAuth
or a still-beta-gated static-header field to authenticate a remote MCP
server -- there is no other way to hand it EKIP's existing bearer-token
scheme. `auth_server_provider=EkipOAuthProvider()` and `auth=AuthSettings(...)`
below turn this `MCPServer` into a real OAuth 2.1 authorization server (per
the MCP Authorization spec: RFC 8414/9728/7591/7636 metadata, dynamic client
registration, PKCE) whose `/authorize` and `/token` endpoints are a thin
front door onto the exact same `core.auth.service` token issuance every
other path already trusts -- see that module for what is and is not new
here. `issuer_url`/`resource_server_url` must be set to this server's real,
publicly-reachable HTTPS base URL (`Settings.mcp_public_base_url`,
`MCP_PUBLIC_BASE_URL` env var) -- `localhost` cannot work here, since
Claude's OAuth discovery calls happen from Anthropic's cloud, not this
machine. `extract_bearer_token`/`app.mcp.auth.resolve_mcp_identity`/
`app.mcp.dispatch.run_mcp_tool` below are unchanged: this is an additional
front-door auth layer, not a replacement for the real identity/RLS
resolution every tool call still runs.

**Verified against the actually-installed `mcp` package (2026-08-06).** The
pinned `mcp>=1.0` requirement turned out to be stale: the environment this
project actually runs in has `mcp==2.0.0` installed, which is a genuine
major-version break from the 1.x `FastMCP` API this module was originally
written against (confirmed by direct inspection of
`mcp/server/mcpserver/__init__.py` and `.../server.py` in the installed
package, not by guessing):
  - There is no `mcp.server.fastmcp` module in 2.0 at all. The equivalent
    class lives at `mcp.server.mcpserver.MCPServer` (renamed from
    `FastMCP`), alongside `mcp.server.mcpserver.Context` (unchanged name).
    The `MCPServer(name=...)` constructor and the `@mcp_server.tool()` /
    `@mcp_server.resource(...)` / `@mcp_server.prompt()` decorators kept the
    same shape as the old FastMCP API, so nothing else in `mcp/tools/` or
    `mcp/resources/` needed to change beyond their `Context` import path.
  - `extract_bearer_token` below now reads `ctx.headers` -- a public
    `Context` property in 2.0 (`Mapping[str, str] | None`, populated by
    HTTP-based transports) -- instead of reaching into
    `ctx.request_context.request.headers` directly. Same data, a
    documented/stable accessor instead of a private-shape guess.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import Context, MCPServer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import PermissionDeniedError
from app.mcp.oauth.provider import EkipOAuthProvider, register_authorization_confirmation_route
from app.shared.config.settings import get_settings

_oauth_provider = EkipOAuthProvider()
_public_base_url = get_settings().mcp_public_base_url

mcp_server = MCPServer(
    name="ekip",
    auth_server_provider=_oauth_provider,
    auth=AuthSettings(
        issuer_url=_public_base_url,
        resource_server_url=_public_base_url,
        client_registration_options=ClientRegistrationOptions(
            enabled=True, valid_scopes=["ekip"], default_scopes=["ekip"]
        ),
        revocation_options=RevocationOptions(enabled=True),
    ),
)
register_authorization_confirmation_route(mcp_server, _oauth_provider)

# Set once, at process startup, by `scripts/run_mcp_server.py` -- see this
# module's docstring. Left `None` until then so an accidental tool call
# before startup wiring runs fails loudly (`run_mcp_tool` below) rather than
# silently doing nothing.
session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]] | None = None

# Set once, at process startup, by `scripts/run_mcp_server.py` -- see this
# module's docstring's "set_tenant_context" section. Same reasoning and same
# fail-loud-if-unset behavior as `session_factory` above.
set_tenant_context: Callable[[AsyncSession, uuid.UUID], Awaitable[None]] | None = None


def extract_bearer_token(ctx: Context) -> str:
    """Pull the caller's bearer access token out of the current MCP
    request's `Authorization` header. See this module's docstring for the
    `mcp` 2.0 API this now reads (`ctx.headers`, not the old private
    `ctx.request_context.request.headers` reach-through).
    """
    headers = ctx.headers
    if headers is None:
        raise PermissionDeniedError(
            "MCP request has no HTTP context to read a bearer token from.",
            error_code="mcp.no_transport_context",
        )

    header = headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise PermissionDeniedError(
            "Missing or malformed Authorization header.",
            error_code="mcp.missing_token",
        )
    return header[len("bearer ") :].strip()
