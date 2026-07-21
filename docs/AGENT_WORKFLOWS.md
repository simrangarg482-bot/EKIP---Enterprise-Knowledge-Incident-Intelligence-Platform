# EKIP — Agent Workflows

Status: **Living document. Update whenever a node, transition, or prompt strategy changes.** This is the detailed companion to `ARCHITECTURE.md` §7; that section stays a summary, this file is the source of truth for the actual graph.

Last updated: 2026-07-20

Framework: LangGraph. Entry points: `agents.answer_question`, `agents.triage_incident`, `agents.generate_postmortem`, `agents.detect_knowledge_gaps` (per `API_DESIGN.md` §2). The first three share one underlying graph with different entry framing; `detect_knowledge_gaps` is a separate, simpler graph described at the end.

---

## 1. Shared Graph State

A single typed state object threaded through every node — never a raw dict, per `ARCHITECTURE.md` §7:

```python
class GraphState(BaseModel):
    # input
    query: str
    incident_id: UUID | None
    actor: Identity

    # retrieval stage
    retrieved_chunks: list[ScoredChunk] = []
    rewritten_query: str | None = None

    # confidence stage
    confidence_score: float | None = None
    confidence_signals: dict[str, float] = {}   # kept for observability/debugging, not just the final number

    # routing
    route: Literal["answer", "investigation"] | None = None

    # investigation stage
    evidence: list[EvidenceItem] = []
    hypotheses: list[RootCauseHypothesis] = []

    # output
    result: AskResponse | None = None

    # control
    retry_count: dict[str, int] = {}    # per-node retry tracking
    terminal_error: str | None = None
```

*Why `confidence_signals` is kept, not just `confidence_score`:* the Confidence Evaluation node's output needs to be debuggable — "why did this get routed to investigation?" must be answerable from stored state, not just re-derived by guessing, especially since the formula itself is still an open item (`ENGINEERING_DECISIONS.md`).

---

## 2. Nodes

### 2.1 Retrieval Agent

**Responsibility:** turn a raw query into ranked, citation-anchored evidence.

**Steps:**
1. *Query understanding/rewriting* — an LLM call that expands abbreviations, resolves incident-context references (e.g. "this error" → the actual error string from `incident_id`'s description), and produces `rewritten_query`. Skipped (pass-through) if `incident_id` is None and the query is already specific — cheap heuristic, not a separate LLM call, to avoid unnecessary latency/cost on already-clear queries.
2. *Hybrid retrieval* — calls `retrieval.search()` (per `API_DESIGN.md` §2) against the relevant collection(s), combining dense + BM25 per `ARCHITECTURE.md` §8.
3. *Reranking* — cross-encoder rerank of the merged candidate set.
4. *Context assembly* — trims to a token budget, preserving `source_offset_start/end` from `DATABASE_DESIGN.md` so every chunk can become a `Citation` with a real excerpt.

**Output:** `retrieved_chunks`, `rewritten_query`.

**Retryable failures:** embedding-service timeout, vector-store connection error — retried with backoff (see §4).
**Terminal condition:** zero chunks returned after retry exhaustion → proceeds to Confidence Evaluation anyway (a confidence score of effectively zero, not a crash) — the empty-evidence case is exactly what the Investigation route exists to handle, not an error state.

---

### 2.2 Confidence Evaluation Node

**Responsibility:** deterministic scoring, no LLM call. Combines signals into `confidence_score` and sets `route`.

**Signals gathered into `confidence_signals`:**
- `top_similarity` — best chunk's vector similarity score
- `rerank_score` — top chunk's cross-encoder score post-rerank
- `source_count` — number of *distinct documents* (not chunks) represented in the top-k, since five chunks from one stale doc is weaker evidence than one chunk each from five sources
- `historical_similarity` — for incident-triage calls only: similarity to past *resolved* incidents specifically (distinct from general document similarity)

**Combination:** a weighted function of the above against a configurable threshold (placeholder implementation — exact weights are an open item in `ENGINEERING_DECISIONS.md`, to be tuned empirically). The important architectural property, independent of the exact formula: this node is pure/deterministic and unit-testable with synthetic `confidence_signals` inputs, with no LLM call and no network I/O — so its routing logic can be tested exhaustively without mocking an LLM.

**Routing:** `route = "answer"` if `confidence_score >= threshold`, else `route = "investigation"`.

---

### 2.3 Answer Agent

**Reached only when** `route == "answer"`.

**Responsibility:** generate the final response from `retrieved_chunks` only.

**Constraint enforced by prompt + a post-generation check:** every factual claim must be traceable to a chunk in `retrieved_chunks`. The post-generation check is a lightweight verification pass (not a second full LLM call by default — an embedding-similarity check between each generated sentence and the retrieved context, escalating to an LLM-based check only if similarity is ambiguously low) that flags ungrounded sentences before they reach the user, rather than trusting the generation prompt alone.

**Output:** populates `result.answer` and `result.citations` (mapping each cited chunk to a `Citation`, per `API_DESIGN.md`).

**Retryable failures:** LLM API timeout/rate-limit — retried with backoff.
**Terminal condition:** if the grounding check fails repeatedly (generated answer can't be grounded in retrieved context even after retry), fall back to a "insufficient grounded information" response rather than emit an ungrounded answer — this is the concrete mechanism behind "never present uncertain conclusions as facts."

---

### 2.4 Investigation Agent

**Reached only when** `route == "investigation"`.

**Responsibility:** evidence-gathering across broader enterprise sources, then hypothesis generation — explicitly separated into two sub-stages so the "verified vs. generated" distinction from `API_DESIGN.md` is structural, not just a prompt instruction.

**Sub-stage A — Evidence gathering (no hypothesis generation yet):**
Searches, in order (short-circuiting once sufficient evidence is found, to bound latency):
1. Recent deployments/commits related to the query's subject (GitHub) — most likely to explain a *new* incident.
2. Related pull requests and their discussion.
3. Slack conversations mentioning similar symptoms.
4. Jira tickets with related labels/components.
5. Existing postmortems (even if below the retrieval confidence threshold for a direct answer — a partial match is still useful investigative context here, unlike in the Answer Agent path).
6. Monitoring/alert metadata — **mocked** in current implementation phase, per the original spec; interface designed so a real integration can replace the mock without changing the graph.

Each result becomes an `EvidenceItem` (per `API_DESIGN.md`) — this sub-stage does no interpretation, only collection.

**Sub-stage B — Hypothesis generation:**
A single LLM call over the assembled `evidence` list, producing `RootCauseHypothesis` entries, each required to cite `supporting_evidence_ids` back into sub-stage A's evidence — a hypothesis with no supporting evidence reference is rejected by a validation step and not surfaced.

**Output:** populates `result.investigation` with `evidence`, `hypotheses`, `suggested_owner_team`, `suggested_next_steps`.

**Retryable failures:** any individual source search failing (e.g. GitHub API rate limit) — that source is skipped with a logged warning, not treated as a fatal error for the whole investigation; partial evidence is still useful.
**Terminal condition:** if *no* evidence is found across all sources, return an `InvestigationResult` with an empty `evidence` list and no hypotheses, plus `suggested_next_steps` that are generic ("no automated evidence found — recommend manual investigation starting with X") rather than fabricating a hypothesis from nothing.

---

### 2.5 Postmortem Agent

**Entry point:** `agents.generate_postmortem`, called after an incident is marked resolved (human action, per `ARCHITECTURE.md` §5 — this agent does not run automatically on every incident).

**Steps:**
1. *Timeline reconstruction* — reads `incident_timeline` (per `DATABASE_DESIGN.md`), merging human notes and any Investigation Agent evidence attached to the incident, into chronological narrative form.
2. *Root cause extraction* — if an Investigation Agent hypothesis exists for this incident and was never contradicted by later timeline entries, it's the starting point for `root_cause`; otherwise derived fresh from the timeline.
3. *Action item generation* — LLM call producing candidate `ActionItem` entries from the root cause and timeline.
4. *Structured report assembly* — populates the `Postmortem` model (`DATABASE_DESIGN.md`/`API_DESIGN.md`), always with `status = "draft"`.

**Output:** a `Postmortem` row created with `status = "draft"`, `generated_by = "agent:postmortem_agent"`.

**No routing logic** — this is a linear pipeline, not a confidence-gated one, because a postmortem draft is never treated as final regardless of how confident the generation was; the human review gate (`/postmortems/{id}/approve`, per `API_DESIGN.md`) is the actual quality gate, not agent self-assessment.

---

### 2.6 Knowledge Gap Agent

**Not part of the per-question graph** — runs as a separate, scheduled graph (or triggered after postmortem approval), per `ARCHITECTURE.md` §7.

**Steps:**
1. Query `agent_executions` (per `DATABASE_DESIGN.md`) for recent low-`confidence_score` executions.
2. Cluster them by topic similarity (embedding-based clustering over `input_summary`) to find repeated gaps rather than one-off low-confidence queries.
3. For clusters above a repetition threshold, generate a `GapReport`: suggested topic, supporting execution IDs, suggested action (new runbook vs. update existing document — determined by checking whether a `documents` row already exists on a closely related topic).

**Output:** `list[GapReport]`, surfaced via `GET /knowledge/gaps` (per `API_DESIGN.md`). **Never** auto-creates a `documents` row — a gap report is a recommendation, not a proposal; turning it into an actual proposed runbook (via `propose_runbook_update`) is a separate, explicit action, keeping this agent's blast radius limited to "suggest," not "write."

---

## 3. Transitions

```
                         ┌─────────────────┐
                         │  Retrieval Agent │
                         └────────┬─────────┘
                                  │
                                  ▼
                   ┌───────────────────────────┐
                   │ Confidence Evaluation Node │
                   └──────────────┬─────────────┘
                                  │
                 ┌────────────────┴────────────────┐
                 │ route == "answer"                │ route == "investigation"
                 ▼                                   ▼
         ┌───────────────┐                  ┌───────────────────┐
         │  Answer Agent │                  │ Investigation Agent│
         └───────┬───────┘                  └──────────┬─────────┘
                 │                                       │
                 ▼                                       ▼
            result populated                     result.investigation populated
                 │                                       │
                 └───────────────┬───────────────────────┘
                                  ▼
                         return AskResponse
```

`generate_postmortem` is a separate linear graph, invoked independently (not a continuation of the above), taking an `incident_id` rather than a `query`.

---

## 4. Failure Handling (applies across all nodes)

- **Retryable failures** (timeouts, rate limits, transient connection errors): up to 2 retries per node with exponential backoff, tracked in `state.retry_count[node_name]`. Exceeding the limit converts the failure to the node's terminal-condition behavior (described per-node above) rather than propagating an unhandled exception up through the graph.
- **Terminal failures** (no evidence found, grounding check repeatedly fails): each node defines its own graceful degradation (described above) — the graph never crashes into a raw 500 for a "the system doesn't know" situation, since that's an expected, designed-for outcome, not a bug.
- **Truly unexpected exceptions** (bug, unhandled type): caught at the graph level, `state.terminal_error` set, `agent_executions.status = "failed"` recorded with `error_detail`, and a generic "something went wrong, this has been logged" response returned — distinct from the "insufficient information" response, so users/on-call engineers can tell "the system doesn't know" apart from "the system broke."

## 5. Human Approval Points (cross-reference)

Restated here for completeness, detailed in `ARCHITECTURE.md` §5 and enforced via `DATABASE_DESIGN.md`'s `status` fields:
- Investigation hypotheses are never embedded into the knowledge base directly — they only become knowledge if they lead to an approved postmortem or an explicitly approved runbook proposal.
- Postmortem drafts require `/postmortems/{id}/approve` before `status` can reach `published`.
- Knowledge Gap Agent output is a recommendation only — never auto-creates a document.

---

## Open items

- Exact confidence-weighting formula (tracked in `ENGINEERING_DECISIONS.md`, still open).
- Grounding-check similarity threshold for the Answer Agent's post-generation verification — to be tuned once real generation data exists.
- Clustering method/threshold for the Knowledge Gap Agent (`k`-means over embeddings vs. a simpler similarity-threshold grouping) — not yet decided.

These will be resolved and logged in `ENGINEERING_DECISIONS.md` as we reach the relevant implementation phase.