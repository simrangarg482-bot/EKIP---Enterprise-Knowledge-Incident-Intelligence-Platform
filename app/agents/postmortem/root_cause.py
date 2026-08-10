"""Postmortem Agent step 2: root-cause extraction (AGENT_WORKFLOWS.md
section 2.5) -- "if an Investigation Agent hypothesis exists for this
incident and was never contradicted by later timeline entries, it's the
starting point for `root_cause`; otherwise derived fresh from the timeline."

Both branches of that rule are handled by one LLM call, not two: rather than
a separate "does a later entry contradict this hypothesis?" classification
step followed by a conditional second generation call, the model is handed
the candidate hypothesis (if any) *and* the full narrative in the same
prompt, with explicit instructions to use the candidate only if nothing
later in the timeline contradicts it, and to derive a fresh root cause
otherwise. A single call that already sees the whole timeline is in a better
position to judge "contradicted by what comes after" than a mechanical
per-entry check would be, and it's the one call this step is scoped to
(AGENT_WORKFLOWS.md doesn't budget two).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from app.agents.llm import log_llm_usage

_NO_CANDIDATE_TEXT = "(none -- no prior Investigation Agent hypothesis exists for this incident)"


def _render_candidate(candidate_hypotheses: list[dict[str, Any]]) -> str:
    """Render the highest-confidence candidate hypothesis (if any) as a short
    text block for the prompt. Only the top one: the model is choosing
    whether to adopt *a* starting point, not weighing several against each
    other -- AGENT_WORKFLOWS.md's rule speaks of "an Investigation Agent
    hypothesis" (singular).
    """
    if not candidate_hypotheses:
        return _NO_CANDIDATE_TEXT

    top = max(candidate_hypotheses, key=lambda h: h.get("confidence", 0) or 0)
    description = top.get("description", "")
    confidence = top.get("confidence", 0)
    return f"{description} (Investigation Agent confidence: {confidence:.2f})"


async def extract_root_cause(
    llm: BaseChatModel, narrative: str, candidate_hypotheses: list[dict[str, Any]]
) -> str:
    """Produce the postmortem's `root_cause` text.

    `narrative` is the full chronological timeline
    (`timeline.build_narrative`'s output); `candidate_hypotheses` is the most
    recent Investigation Agent run's hypotheses for this incident
    (`timeline.latest_investigation_hypotheses`'s output, possibly empty).
    """
    candidate_text = _render_candidate(candidate_hypotheses)
    prompt = (
        "You are writing the root-cause section of an incident postmortem. "
        "Use ONLY the timeline and candidate hypothesis below -- do not "
        "invent facts not present in the timeline.\n\n"
        f"Timeline:\n{narrative}\n\n"
        f"Candidate root-cause hypothesis from a prior automated "
        f"investigation: {candidate_text}\n\n"
        "If the candidate hypothesis is present and nothing later in the "
        "timeline contradicts it, use it as the basis for the root cause "
        "(you may tighten the wording, but do not change its substance). "
        "If it is absent, or a later timeline entry contradicts it, ignore "
        "it and derive the root cause fresh from the timeline instead. "
        "Respond with ONLY the root-cause text itself -- one to three "
        "sentences, no headings, no preamble, no markdown. If the timeline "
        "does not contain enough information to determine a root cause, "
        "respond with exactly: 'Root cause could not be determined from "
        "the available timeline; manual review required.'"
    )
    response = await llm.ainvoke(prompt)
    log_llm_usage("postmortem", llm, response)
    root_cause = str(response.content).strip()
    return root_cause
