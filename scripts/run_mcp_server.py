"""EKIP MCP server process entrypoint."""

from __future__ import annotations

from mcp.server.transport_security import TransportSecuritySettings

from app.database.session import session_scope, set_tenant_context
from app.mcp.servers import main as mcp_assembly
from app.mcp.servers import server as server_module
from app.shared.config.logging import configure_logging
from app.shared.config.settings import get_settings


configure_logging()

# Trigger registration of all tools, resources, and prompts.
_ = mcp_assembly.mcp_server

# Inject database dependencies.
server_module.session_factory = session_scope
server_module.set_tenant_context = set_tenant_context


if __name__ == "__main__":
    # NOTE: `app.mcp.servers.server`'s OAuth wiring (`AuthSettings.issuer_url`/
    # `resource_server_url`, for Claude's remote-connector OAuth flow) reads
    # `Settings.mcp_public_base_url` (`MCP_PUBLIC_BASE_URL` env var) -- keep it
    # pointed at the SAME public hostname as the ngrok entry in `allowed_hosts`
    # below, or OAuth discovery will advertise a URL Claude can't reach.
    #
    # `TransportSecurityMiddleware._validate_host` (mcp.server.transport_security)
    # requires an EXACT match against `allowed_hosts`, or an entry ending in
    # `:*` to match any port on that base host. "localhost"/"127.0.0.1" with no
    # port never match a real request's `Host` header, which always includes
    # this server's actual port (e.g. "127.0.0.1:8001") -- every local/direct
    # request (including `scripts/live_mcp_tests/`, which targets
    # http://127.0.0.1:8001/mcp by default) was getting rejected with `421
    # Invalid Host header` before reaching MCP dispatch at all, a transport-
    # layer failure a tool call never even sees. Using the `:*` wildcard form
    # (rather than a specific port) means this list needs no edit at all when
    # `MCP_PORT` changes below. The ngrok hostname has no port on its public
    # HTTPS endpoint (443, omitted from `Host`), so it matches exactly as-is.
    transport_security = TransportSecuritySettings(
        allowed_hosts=[
            "localhost:*",
            "127.0.0.1:*",
            "relic-heaviness-handsfree.ngrok-free.dev",
        ]
    )

    port = get_settings().mcp_port
    print(f"Starting EKIP MCP server on http://127.0.0.1:{port} (MCP_PORT to override) ...")
    server_module.mcp_server.run(
        transport="streamable-http",
        port=port,
        transport_security=transport_security,
    )