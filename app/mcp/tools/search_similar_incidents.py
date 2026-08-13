"""MCP tool: `search_similar_incidents` (API_DESIGN.md section 3).

Wraps `agents.search_similar_incidents`, not `retrieval.search` directly --
`app.mcp` may not import `app.retrieval` at all (pyproject.toml's "mcp never
touches database or retrieval directly" contract forbids that *direct*
import, not just transitive chains), so the thin `SearchFilters`-resolution
wrapper lives in `agents/service.py` instead (see that function's own
docstring for why, and for the flagged gap versus API_DESIGN.md's literal
`collection="incidents"` wording -- no such collection exists).
"""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import Context
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import service as agents_service
from app.mcp.dispatch import run_mcp_tool
from app.mcp.servers.server import extract_bearer_token, mcp_server
from app.shared.schemas import Identity


@mcp_server.tool()
async def search_similar_incidents(description: str, ctx: Context) -> list[dict[str, Any]]:
    """Find past EKIP incidents whose symptoms resemble the given
    description, e.g. "checkout returning 500 errors" or "payments API
    timing out after deploy". Use this to check whether something similar
    has happened before, rather than `ask_question` when the user
    specifically wants a list of comparable past incidents rather than a
    synthesized answer.

    Returns: `{description: str}` -> `list[ScoredChunk]` (serialized).
    """
    raw_token = extract_bearer_token(ctx)

    async def handler(session: AsyncSession, identity: Identity) -> list[dict[str, Any]]:
        results = await agents_service.search_similar_incidents(session, description, identity)
        return [chunk.model_dump(mode="json") for chunk in results]

    return await run_mcp_tool(
        tool_name="search_similar_incidents",
        raw_token=raw_token,
        request_summary={"description": description},
        handler=handler,
    )
