"""MCP tool: `investigate_incident` (API_DESIGN.md section 3).

Wraps `agents.triage_incident` -- routes straight into the Investigation
Agent, bypassing the confidence-routed answer/investigation split
`ask_question` uses. Mirrors `app.api.routers.ask.investigate_incident`'s
identical reasoning for the REST equivalent.
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
async def investigate_incident(incident_id: str, ctx: Context) -> dict[str, Any]:
    """Run a full root-cause investigation on one specific, already-known
    EKIP incident (given its UUID) -- gathering evidence (related code
    changes, similar past incidents, relevant docs) and producing hypotheses,
    not just a grounded answer. Use this instead of `ask_question` when the
    user already has a specific incident ID in hand and wants a deep
    investigation rather than a quick answer.

    Returns: `{incident_id: str}` -> `AskResponse` (with `investigation`
    populated).
    """
    raw_token = extract_bearer_token(ctx)
    parsed_incident_id = uuid.UUID(incident_id)

    async def handler(session: AsyncSession, identity: Identity) -> dict[str, Any]:
        result = await agents_service.triage_incident(
            session, parsed_incident_id, identity, trigger_source="mcp"
        )
        return result.model_dump(mode="json")

    return await run_mcp_tool(
        tool_name="investigate_incident",
        raw_token=raw_token,
        request_summary={"incident_id": incident_id},
        handler=handler,
    )
