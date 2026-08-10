"""Postmortem Agent step 3: action-item generation (AGENT_WORKFLOWS.md
section 2.5) -- "LLM call producing candidate `ActionItem` entries from the
root cause and timeline."

Same plain-text-JSON-prompt-plus-manual-parse convention as
`agents.investigation.hypothesis` (see that module's docstring for why no
LangChain structured-output helper is used) -- the second place in this
codebase asking an LLM for a JSON array and parsing it defensively, not yet
a third occurrence that would justify extracting a shared parsing helper
(mirroring the "not extracted... a third would make the case" threshold
`core.incidents.service._ensure_same_organization`'s own docstring already
uses for the same kind of judgment call).

Every generated item is always created `status="open"` regardless of
anything the model produces for that field -- these are freshly proposed
action items on a draft postmortem nobody has reviewed yet; a model that
hallucinated `"done"` would be actively misleading, so `status` is not
model-controlled at all, unlike `description`/`owner`.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.language_models import BaseChatModel

from app.agents.llm import log_llm_usage
from app.core.incidents.schemas import ActionItem
from app.shared.config.logging import get_logger

logger = get_logger(__name__)


async def generate_action_items(
    llm: BaseChatModel, narrative: str, root_cause: str
) -> list[ActionItem]:
    """Produce candidate `ActionItem`s from `narrative` and `root_cause`.

    Never raises on a malformed model response -- unlike
    `agents.investigation.hypothesis.generate_hypotheses` (which the
    Investigation Agent retries fresh on a parse failure), a postmortem
    draft with zero action items is a legitimate, reviewable output (a human
    reviewer can always add their own during approval), so a parse failure
    here degrades to an empty list plus a logged warning rather than
    propagating a retry all the way up through `agents.service.
    generate_postmortem`.
    """
    prompt = (
        "You are proposing follow-up action items for an incident "
        "postmortem, based ONLY on the timeline and root cause below -- do "
        "not invent facts not present in them.\n\n"
        f"Timeline:\n{narrative}\n\n"
        f"Root cause: {root_cause}\n\n"
        "Respond with ONLY a JSON array (no markdown code fences, no "
        "commentary) of objects with exactly this shape:\n"
        '[{"description": "short, concrete, actionable step", "owner": '
        '"team or role name, or null if unclear"}]\n\n'
        "Propose at most 5 items. If the root cause is too uncertain to "
        "propose concrete action items, return an empty array `[]` rather "
        "than a vague placeholder."
    )
    response = await llm.ainvoke(prompt)
    log_llm_usage("postmortem", llm, response)
    raw_text = str(response.content).strip()

    parsed = _parse_response(raw_text)
    if parsed is None:
        return []
    return _validate_action_items(parsed)


def _parse_response(raw_text: str) -> list[Any] | None:
    """Parse `raw_text` as a JSON array, tolerating a markdown code fence
    the model added despite being told not to (same tolerance
    `agents.investigation.hypothesis._parse_response` applies). Returns
    `None` for anything that isn't a JSON array.
    """
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[len("json") :].strip()

    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("postmortem_action_items_parse_failed", error=str(exc))
        return None

    if not isinstance(parsed, list):
        logger.warning(
            "postmortem_action_items_unexpected_shape", raw_type=type(parsed).__name__
        )
        return None
    return parsed


def _validate_action_items(raw_items: list[Any]) -> list[ActionItem]:
    """Keep only well-formed items, always forcing `status="open"` -- see
    module docstring.
    """
    validated: list[ActionItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue

        description = raw.get("description")
        if not isinstance(description, str) or not description.strip():
            continue

        owner = raw.get("owner")
        if not isinstance(owner, str) or not owner.strip():
            owner = None

        validated.append(ActionItem(description=description.strip(), owner=owner, status="open"))
    return validated
