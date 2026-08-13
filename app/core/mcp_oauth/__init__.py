"""Persistent storage for MCP OAuth (RFC 7591) client registrations.

Exists purely to give `app.mcp.oauth.provider.EkipOAuthProvider` a
`core`-side function to call for its client registry -- `app.mcp` cannot
import `app.database` in any form (see `app.mcp.servers.server`'s module
docstring), the same reason `core.observability` exists for `mcp/`'s
request-logging path. See `service.py` for why this registry needs to be
persistent at all.
"""

from __future__ import annotations
