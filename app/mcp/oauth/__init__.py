"""The MCP OAuth bridge (ARCHITECTURE.md section 6 / PROJECT_PLAN.md section 7.4
follow-up): lets an MCP client that only supports OAuth 2.1 (no static
`Authorization` header field) -- Claude's "Add custom connector" UI, as of
2026-08, is exactly this -- obtain and refresh a real EKIP access token
without EKIP growing a second, divergent authentication system.

See `provider.py`'s module docstring for the full design and why this is a
*bridge* (a spec-shaped front door onto EKIP's existing, real
`core.auth.service` token issuance) rather than a new identity system.
"""

from __future__ import annotations
