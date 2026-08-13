"""MCP tool: `ask_question` (API_DESIGN.md section 3).

Thin translation only, per ARCHITECTURE.md section 6: validate input,
resolve `Identity` (via `run_mcp_tool`), call `agents.answer_question`,
shape the result. `trigger_source="mcp"` is passed explicitly (unlike the
REST layer's equivalent endpoint, `app.api.routers.ask.ask_question`, which
leaves it at its `"core_api"` default) so `agent_executions` rows correctly
attribute calls that arrived over MCP rather than REST -- exactly the
distinction `shared.schemas.TriggerSource`'s vocabulary exists to make.
"""

from __future__ import annotations

import uuid
from typing import Any

from mcp.server.mcpserver import Context
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import service as agents_service
from app.mcp.dispatch import run_mcp_tool
from app.mcp.servers.server import extract_bearer_token, mcp_server
from app.shared.schemas import Identity


@mcp_server.tool()
async def ask_question(query: str, ctx: Context, incident_id: str | None = None) -> dict[str, Any]:
    """Answer a question about this organization's incidents, runbooks, and
    engineering knowledge, grounded in EKIP's own ingested data (past
    incidents, GitHub/Confluence/Slack/Jira history, runbooks) -- not general
    knowledge. Use this whenever the user asks what happened in an incident,
    why something broke, how a system works, where something is documented,
    or how a past incident was resolved.

    Routes internally to either a direct grounded answer or a full
    investigation, depending on EKIP's own confidence -- the caller does not
    need to decide which.

    Args:
        query: The natural-language question to answer.
        incident_id: Optional -- scope the answer to one specific incident
            (its UUID) if the question is about a particular incident already
            identified elsewhere in the conversation.

    Returns: `{query: str, incident_id?: str}` -> `AskResponse` (serialized).
    """
    raw_token = extract_bearer_token(ctx)
    parsed_incident_id = uuid.UUID(incident_id) if incident_id else None

    async def handler(session: AsyncSession, identity: Identity) -> dict[str, Any]:
        result = await agents_service.answer_question(
            session, query, parsed_incident_id, identity, trigger_source="mcp"
        )
        return result.model_dump(mode="json")

    return await run_mcp_tool(
        tool_name="ask_question",
        raw_token=raw_token,
        request_summary={"query": query, "incident_id": incident_id},
        handler=handler,
    )
