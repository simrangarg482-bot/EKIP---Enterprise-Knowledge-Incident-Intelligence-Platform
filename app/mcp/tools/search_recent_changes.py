"""MCP tool: `search_recent_changes` (API_DESIGN.md section 3).

Wraps `agents.search_recent_changes`, not `retrieval.search` directly, for
the same reason `search_similar_incidents.py` does -- see this module's
sibling and `agents.service.search_recent_changes`'s own docstring for the
flagged best-effort nature of `since`-based recency filtering.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver import Context
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import service as agents_service
from app.mcp.dispatch import run_mcp_tool
from app.mcp.servers.server import extract_bearer_token, mcp_server
from app.shared.schemas import Identity


@mcp_server.tool()
async def search_recent_changes(
    query: str,
    ctx: Context,
    since: str | None = None,
    repository: str | None = None,
    collection: str | None = None,
) -> list[dict[str, Any]]:
    """Find recently ingested code/config/doc changes (e.g. GitHub commits,
    deploys, config edits) relevant to `query` -- use this to check "what
    changed recently that could explain this" during an investigation,
    optionally narrowed to changes after a given time with `since`, or to
    one GitHub repository (e.g. `"owner/name"`) with `repository`.

    `collection` is one of `"code"`/`"documentation"`/`"conversations"`
    (`retrieval.schemas.CollectionName`, typed as plain `str` here rather
    than imported -- `app.mcp` may not import `app.retrieval` directly,
    per this project's import-linter contract; `agents_service.
    search_recent_changes` is what actually validates it). Defaults to
    `"code"` (source files). Most of a typical GitHub repo's actual
    ingested content -- issues, pull requests, commit messages, READMEs --
    lands in `"documentation"` instead, not `"code"`; a repo with no source
    files at all (e.g. docs-only) will return nothing unless you explicitly
    pass `collection="documentation"`. `repository` is only usable with
    `collection` `"code"` or `"documentation"` -- `"conversations"` (chat)
    has no repository concept.

    Returns: `{query: str, since?: str, repository?: str, collection?: str}`
    -> `list[ScoredChunk]` (serialized).

    `since`, if given, must be an ISO-8601 timestamp -- a malformed value
    raises a plain `ValueError`, which `run_mcp_tool`'s caller will surface
    as an unexpected (500-mapped) failure rather than a clean 400, a known,
    minor rough edge left as-is rather than adding a bespoke `EKIPError`
    subclass for one MCP tool's input parsing.
    """
    raw_token = extract_bearer_token(ctx)
    parsed_since = datetime.fromisoformat(since) if since else None

    async def handler(session: AsyncSession, identity: Identity) -> list[dict[str, Any]]:
        kwargs = {"since": parsed_since, "repository": repository}
        if collection is not None:
            kwargs["collection"] = collection
        results = await agents_service.search_recent_changes(session, query, identity, **kwargs)
        return [chunk.model_dump(mode="json") for chunk in results]

    return await run_mcp_tool(
        tool_name="search_recent_changes",
        raw_token=raw_token,
        request_summary={
            "query": query,
            "since": since,
            "repository": repository,
            "collection": collection,
        },
        handler=handler,
    )
