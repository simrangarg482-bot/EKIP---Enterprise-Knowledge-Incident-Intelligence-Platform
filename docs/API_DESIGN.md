# EKIP — API Design

Status: **Living document. Update whenever an endpoint, internal interface, or MCP tool contract changes.** This is the detailed companion to `ARCHITECTURE.md` §6 (MCP) and the module public-interface summaries in §3; this file is the source of truth for actual request/response shapes.

Last updated: 2026-07-20

Scope: three layers, all documented here because they share the same underlying contracts —
1. **Core REST API** — the `core/` module's HTTP surface.
2. **Internal module interfaces** — the Pydantic-typed function signatures modules call across boundaries (per `ARCHITECTURE.md` §4, these *are* the future service contracts).
3. **MCP tool contracts** — thin translations of (1) and (2) for MCP clients, per `ARCHITECTURE.md` §6.

---

## Design conventions

- All request/response bodies are Pydantic models, defined once in `shared/schemas/` or the owning module, and reused verbatim across REST, internal calls, and MCP — never redefined per-layer. This is what makes extraction later a matter of adding a transport adapter, not rewriting contracts (per `ARCHITECTURE.md` §4).
- REST responses follow a consistent envelope: the resource (or list) directly, with pagination via `limit`/`offset` query params and a `X-Total-Count` header for list endpoints — no nested `{data: ...}` wrapper, since it adds no value here and every client would need to unwrap it.
- Errors follow a single shape: `{"error_code": str, "message": str, "detail": dict | None}`, mapped to conventional HTTP status codes (400 validation, 401/403 auth, 404 not found, 409 conflict, 500 unexpected).
- Every mutating endpoint requires a resolved identity (user or service) and results in an `audit_logs` entry, per `DATABASE_DESIGN.md`.

---

## 1. Core REST API

### Auth

| Method | Path | Purpose |
|---|---|---|
| POST | `/auth/login` | Exchange credentials for a session/JWT |
| POST | `/auth/refresh` | Refresh an expired token |
| GET | `/auth/me` | Resolve current identity + roles |

### Incidents

| Method | Path | Purpose |
|---|---|---|
| POST | `/incidents` | Create an incident |
| GET | `/incidents/{id}` | Fetch one incident |
| GET | `/incidents` | List, filterable by `status`, `severity`, `owner_team` |
| PATCH | `/incidents/{id}` | Update status/severity/owner |
| GET | `/incidents/{id}/timeline` | Fetch timeline entries |
| POST | `/incidents/{id}/timeline` | Add a manual timeline note (human-authored) |

**`IncidentCreate` (request body):**
```python
class IncidentCreate(BaseModel):
    title: str
    description: str
    severity: Literal["low", "medium", "high", "critical"]
```

**`Incident` (response body):**
```python
class Incident(BaseModel):
    id: UUID
    title: str
    description: str
    status: Literal["open", "investigating", "resolved", "closed"]
    severity: Literal["low", "medium", "high", "critical"]
    owner_team: str | None
    reported_by: UUID
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime
```

### Ask / Question answering

| Method | Path | Purpose |
|---|---|---|
| POST | `/ask` | Ask a question; routes into `agents.answer_question` |

**`AskRequest`:**
```python
class AskRequest(BaseModel):
    query: str
    incident_id: UUID | None = None  # optional: grounds the question in incident context
```

**`AskResponse`** — this shape is the one referenced repeatedly below; it's the contract that must make the verified/retrieved/hypothesis distinction from `ARCHITECTURE.md` §5 explicit, not implicit in prose:
```python
class Citation(BaseModel):
    document_id: UUID
    chunk_id: UUID
    source_url: str | None
    excerpt: str

class AskResponse(BaseModel):
    confidence: float
    route_taken: Literal["answer", "investigation"]
    answer: str | None                # populated only when route_taken == "answer"
    citations: list[Citation]          # populated only when route_taken == "answer"
    investigation: "InvestigationResult | None"  # populated only when route_taken == "investigation"
```

**`InvestigationResult`** — deliberately separates verified evidence from AI hypothesis, per the "never present uncertain conclusions as facts" requirement:
```python
class EvidenceItem(BaseModel):
    source: Literal["github", "pull_request", "commit", "slack", "jira", "deployment", "postmortem"]
    reference: str          # PR number, message link, ticket ID, etc.
    summary: str
    retrieved_at: datetime

class InvestigationResult(BaseModel):
    evidence: list[EvidenceItem]                 # verified/retrieved, not generated
    hypotheses: list["RootCauseHypothesis"]        # explicitly AI-generated
    suggested_owner_team: str | None
    suggested_next_steps: list[str]

class RootCauseHypothesis(BaseModel):
    description: str
    confidence: float
    supporting_evidence_ids: list[str]   # references EvidenceItem.reference values
```

### Postmortems

| Method | Path | Purpose |
|---|---|---|
| POST | `/incidents/{id}/postmortem` | Trigger draft generation via `agents.generate_postmortem` |
| GET | `/postmortems/{id}` | Fetch a postmortem (draft or published) |
| PATCH | `/postmortems/{id}` | Human edits during review |
| POST | `/postmortems/{id}/approve` | Human-review gate — required before publish |

**`Postmortem`:**
```python
class ActionItem(BaseModel):
    description: str
    owner: str | None
    status: Literal["open", "in_progress", "done"]

class Postmortem(BaseModel):
    id: UUID
    incident_id: UUID
    status: Literal["draft", "in_review", "approved", "published"]
    root_cause: str | None
    action_items: list[ActionItem]
    generated_by: str   # "agent:postmortem_agent" or "user:<id>", per DATABASE_DESIGN.md convention
    reviewed_by: UUID | None
```

*Why `/postmortems/{id}/approve` is a distinct endpoint rather than a generic PATCH to `status`:* approval is the human-in-the-loop gate called out repeatedly in `ARCHITECTURE.md` — giving it its own endpoint makes it a first-class, auditable action (easy to permission separately as `postmortem:approve`) rather than one of many fields a PATCH could silently change.

### Knowledge review queue

| Method | Path | Purpose |
|---|---|---|
| GET | `/knowledge/proposed` | List `documents` with `status = proposed` (agent-authored runbook/doc proposals awaiting review) |
| POST | `/knowledge/{document_id}/publish` | Human-review gate — flips `documents.status` to `published`, triggering embedding into the retrieval index |
| POST | `/knowledge/{document_id}/reject` | Discards a proposal |

### Knowledge gaps

| Method | Path | Purpose |
|---|---|---|
| GET | `/knowledge/gaps` | List current recommendations from the Knowledge Gap Agent |

---

## 2. Internal module interfaces

These are the actual Python call signatures modules use across boundaries — written to match the REST shapes above wherever they overlap, so REST handlers are thin pass-throughs.

### `core/` public interface (called by `mcp/`)
```python
def create_incident(actor: Identity, data: IncidentCreate) -> Incident: ...
def get_incident(actor: Identity, incident_id: UUID) -> Incident: ...
def update_incident(actor: Identity, incident_id: UUID, patch: IncidentUpdate) -> Incident: ...
def add_timeline_note(actor: Identity, incident_id: UUID, note: str) -> TimelineEntry: ...
def approve_postmortem(actor: Identity, postmortem_id: UUID) -> Postmortem: ...
def publish_document(actor: Identity, document_id: UUID) -> Document: ...
def authorize(actor: Identity, permission_code: str) -> bool: ...
def record_audit_event(actor: Identity, action: str, resource_type: str, resource_id: UUID, metadata: dict) -> None: ...
```

### `agents/` public interface (called by `core/` and `mcp/`)
```python
def answer_question(query: str, incident_id: UUID | None, actor: Identity) -> AskResponse: ...
def triage_incident(incident_id: UUID, actor: Identity) -> AskResponse: ...
def generate_postmortem(incident_id: UUID, actor: Identity) -> Postmortem: ...
def detect_knowledge_gaps() -> list[GapReport]: ...
```

*`Identity` is threaded through every call* — this is what makes MCP-vs-REST access control identical, per `ARCHITECTURE.md` §6: the same `authorize()` check runs regardless of entry point.

### `retrieval/` public interface (called by `agents/`)
```python
def search(query: str, collection: str, filters: dict, top_k: int) -> list[ScoredChunk]: ...
def upsert(collection: str, chunks: list[ChunkInput]) -> None: ...
```

### `ingestion/` public interface (called by `core/`, for triggering/status)
```python
def run_ingestion_job(source: str, source_config: dict) -> IngestionJob: ...
def get_job_status(job_id: UUID) -> IngestionJob: ...
```

---

## 3. MCP tool contracts

Each tool below is a thin wrapper: validate MCP input → resolve `Identity` from the MCP auth token → call the internal interface above → shape the result as an MCP tool response. No tool body contains logic beyond this translation, per `ARCHITECTURE.md` §6.

| MCP tool | Wraps | Input | Output |
|---|---|---|---|
| `ask_question` | `agents.answer_question` | `{query: str, incident_id?: str}` | `AskResponse` (serialized) |
| `investigate_incident` | `agents.triage_incident` | `{incident_id: str}` | `AskResponse` (with `investigation` populated) |
| `search_similar_incidents` | `retrieval.search` (collection=`incidents`) | `{description: str}` | `list[ScoredChunk]` (serialized) |
| `search_recent_changes` | `retrieval.search` (collection=`code`, filtered by recency) | `{query: str, since?: str}` | `list[ScoredChunk]` |
| `generate_postmortem` | `agents.generate_postmortem` | `{incident_id: str}` | `Postmortem` (status will be `draft`) |
| `propose_runbook_update` | `core`'s document-proposal path (creates a `documents` row with `status=proposed`) | `{title: str, content: str, source_incident_id?: str}` | `Document` (status `proposed`) |

**Resources exposed:**
- `incident://{id}` → resolves to `core.get_incident`
- `document://{id}` → resolves to a read-only document fetch (published documents only, unless the requesting identity has `knowledge:review` permission)

**Prompts exposed:**
- `triage-incident` — wraps `investigate_incident`, pre-filled with a triage-oriented system framing
- `draft-postmortem` — wraps `generate_postmortem`

Every tool call is logged to `mcp_requests` (per `DATABASE_DESIGN.md`) with `tool_name`, resolved `identity`, and latency — this is the data source for the MCP latency metrics referenced in the original spec's observability section.

---

## Open items

- Pagination cursor style (offset-based above; may move to keyset pagination once `incidents`/`documents` volumes are known — noted here rather than in `ENGINEERING_DECISIONS.md` since no decision has been made yet either way).
- Whether `search_recent_changes` needs its own dedicated retrieval collection or can filter the existing `code` collection by metadata recency — depends on `retrieval/` implementation details not yet built.
- Rate limiting strategy per MCP identity — not yet designed.

These will be resolved and, where they represent a real architectural choice, logged in `ENGINEERING_DECISIONS.md`.