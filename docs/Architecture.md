# EKIP — Enterprise Knowledge & Incident Intelligence Platform

## Architecture Document (Source of Truth)

Status: **Living document.** Update this file whenever an architectural decision changes. See `ENGINEERING_DECISIONS.md` for the reasoning history behind each decision, and `PROJECT_STATUS.md` for what's actually been built so far.

---

## 1. Project Overview

### Problem

Software engineering teams accumulate knowledge across many disconnected systems — documentation wikis, runbooks, Slack threads, GitHub history, Jira tickets, and postmortems. When an incident happens, the knowledge needed to resolve it quickly is usually *somewhere* in that sprawl, but engineers waste time searching multiple tools, or worse, re-solve a problem that was already solved months ago because nobody could find the record of it.

A second, harder problem: some incidents are genuinely novel. No runbook, no postmortem, no prior art exists. A naive RAG system will still confidently retrieve *something* and let an LLM generate a plausible-sounding but ungrounded answer. In an incident-response context, a confident wrong answer is worse than no answer.

### Who uses this system

- **On-call engineers**, during an active incident, who need fast, cited answers or — when no answer exists — a structured investigation starting point instead of silence or hallucination.
- **Engineers doing routine work**, asking general knowledge questions (via chat client or IDE, through MCP) about how a system works, where something is documented, or how a past incident was resolved.
- **Incident commanders / team leads**, generating postmortems and reviewing AI-proposed documentation before it becomes part of the permanent knowledge base.
- **Platform/knowledge owners**, who care about knowledge-gap detection — i.e., which parts of the system are under-documented, based on how often retrieval fails or confidence stays low.

### Why this architecture is suitable

The system has three fundamentally different workload shapes that all need to share the same knowledge base and business logic:

1. **Low-latency, transactional** work (auth, incident CRUD, audit logging).
2. **Bursty, long-running, I/O-heavy** work (pulling from Slack/GitHub/Jira, chunking, embedding).
3. **LLM-bound, multi-step, stateful** work (agent orchestration, investigation workflows, human-in-the-loop review).

Rather than guessing up front which of these needs to scale independently, the system is built as a **modular monolith with strict internal boundaries**, so any of the three can be extracted into its own service later, backed by evidence of an actual scaling or ownership need — not by speculation.

### Core capabilities

- Cited, retrieval-grounded Q&A over enterprise knowledge sources.
- Incident triage: similar-incident retrieval, root-cause suggestion, team-ownership prediction.
- Confidence-aware routing: known knowledge is answered directly; unknown territory triggers an explicit Investigation workflow instead of a hallucinated answer.
- Automated postmortem drafting, with mandatory human review before publication.
- Continuous knowledge-gap detection and a closed feedback loop from "unknown incident" → "resolved" → "reviewed" → "embedded knowledge available for next time."
- MCP as the interface layer, so any MCP-compatible AI client (Claude Desktop, Claude Code, internal tools) can use these capabilities through a stable tool contract.

---

## 2. Architecture Decision: Modular Monolith with Extraction Seams

**Decision:** Build EKIP as a single deployable application composed of strictly-bounded internal modules, rather than as microservices from day one.

### Why this is better for a solo developer

- **One codebase, one deployment, one database connection pool to reason about.** Microservices multiply operational surface area — service discovery, distributed tracing across process boundaries, network failure handling, versioned API contracts between services — none of which teaches you anything about *this* platform's actual hard problems (retrieval quality, confidence scoring, agent orchestration).
- **Refactoring is cheap inside a monolith.** Module boundaries can be corrected as understanding improves. A wrong microservice boundary is expensive to undo — it requires a network-level migration, not a code move.
- **Debugging is a stack trace, not a distributed trace.** For a solo developer without a dedicated observability team, this matters enormously during development.

### How it maintains production-level engineering practices

Boundary discipline is enforced the same way it would be in a microservices system — through interfaces, not proximity:

- Every module exposes a small, explicit public interface (a Python `Protocol` or a facade module of top-level functions). Other modules may only import from that public interface, never from a module's internals (its ORM models, its private helpers, its DB session).
- Every module owns its own data. If `ingestion` needs data that `core` owns, it calls into `core`'s public interface — it does not query `core`'s tables directly.
- Every cross-module call is written as if it could become a network call tomorrow: it takes serializable arguments, returns serializable results, and does not pass live DB sessions or ORM objects across the boundary.

This last rule is the single most important discipline in the whole document — violating it is what turns a "modular monolith" into a monolith with modules in name only.

### How modules can later be extracted

Because every cross-module call already looks like a function that *could* be an RPC call, extraction is mechanical:

1. Pick the module under real scaling/ownership pressure (most likely candidates: `ingestion` first, due to bursty load; `mcp` second, if external AI-client traffic grows large).
2. Stand it up as its own FastAPI (or worker) process with the same public interface, now exposed over HTTP/queue instead of Python function calls.
3. Replace the in-process call in the caller with an HTTP client / queue producer implementing the same interface (a thin adapter — the caller's code barely changes because it was already coded against an interface, not an implementation).
4. No other module needs to change, because nothing outside the extracted module ever depended on its internals.

---

## 3. Module Design

```
app/
├── core/         # auth, users, incidents, postmortems, audit — transactional heart
├── mcp/          # MCP server(s): tools, resources, prompts — interface layer only
├── agents/       # LangGraph orchestration: retrieval, investigation, postmortem, knowledge-gap agents
├── ingestion/    # connectors, document processing, chunking, embedding — background workers
├── retrieval/    # Qdrant / pgvector abstraction, hybrid search, reranking — a library, not a service
├── database/     # SQLAlchemy models, migrations, session management — shared persistence layer
└── shared/       # cross-cutting: config, logging, exceptions, common Pydantic schemas
```

### `core/`

- **Responsibility:** the transactional, user-facing surface — authentication, authorization (RBAC), user/role management, incident records, postmortem records, audit logging.
- **Owns:** the `users`, `roles`, `incidents`, `postmortems`, `audit_logs` tables (via `database/`), and all business rules around who can do what.
- **Must not do:** call external APIs (Slack/GitHub/Jira), run embedding models, or contain LangGraph agent logic. If a request needs an AI-generated answer, `core` calls into `agents`' public interface — it does not reimplement any retrieval logic itself.
- **Public interface:** service-layer functions such as `create_incident(...)`, `get_incident(...)`, `record_audit_event(...)`, `authorize(user, action, resource)`.
- **Can call:** `agents` (to answer a question or trigger investigation/postmortem generation), `database`, `shared`.
- **Cannot call:** `ingestion` internals, `retrieval` internals, `mcp` (dependency direction is one-way: `mcp` depends on `core`, not the reverse).

### `mcp/`

- **Responsibility:** expose EKIP's capabilities to MCP clients as tools/resources/prompts. Pure interface/adapter layer.
- **Owns:** MCP protocol handling, request/response translation, MCP-level auth scope checks.
- **Must not do:** contain business logic, touch the database directly, or call external knowledge-source APIs directly. Every MCP tool handler's body should be "translate MCP args → call `core` or `agents` public interface → translate result back to MCP response."
- **Public interface:** the MCP server itself (its declared tools are the interface, from the outside world's perspective).
- **Can call:** `core`, `agents` (both through their public interfaces only).
- **Cannot call:** `database`, `retrieval`, `ingestion` directly.

### `agents/`

- **Responsibility:** the LangGraph multi-agent orchestration — Retrieval Agent, Confidence Evaluation node, Investigation Agent, Answer Agent, Postmortem Agent, Knowledge Gap Agent — and the state graph connecting them.
- **Owns:** agent state schemas, prompt templates, graph definition, routing/decision logic (e.g., confidence-threshold routing).
- **Must not do:** own its own copy of business data — it reads incidents/postmortems through `core`'s interface, not by querying tables directly. Must not perform raw embedding/chunking (that's `ingestion`'s and `retrieval`'s job) — it calls `retrieval` for search.
- **Public interface:** functions like `answer_question(query, context) -> AgentResult`, `triage_incident(incident_id) -> TriageResult`, `generate_postmortem(incident_id) -> PostmortemDraft`, `detect_knowledge_gaps() -> list[GapReport]`.
- **Can call:** `retrieval`, `core` (read-only lookups + writing back results like postmortem drafts), `shared`.
- **Cannot call:** `mcp`, `ingestion` internals (it may trigger an ingestion job via `core`'s job-tracking interface, but does not import ingestion connector code).

### `ingestion/`

- **Responsibility:** pull raw content from external sources, process it into clean documents, chunk it, generate embeddings, and write it into the vector + metadata stores.
- **Owns:** connector implementations (Slack, GitHub, Jira, docs), the processing/chunking pipeline, embedding job execution, ingestion job status.
- **Must not do:** answer user questions, run agent logic, or expose anything over MCP directly.
- **Public interface:** `run_ingestion_job(source_config) -> JobResult`, `reindex(document_id)`.
- **Can call:** `retrieval` (to write vectors), `database` (job/document metadata), `shared`.
- **Cannot call:** `agents`, `mcp`, `core` internals (may report job completion through a `core`-owned job-status interface).

### `retrieval/`

- **Responsibility:** a storage-agnostic retrieval abstraction — hybrid search (dense + BM25), reranking, similarity scoring — with pluggable backends (Qdrant, pgvector).
- **Owns:** the `VectorStore` interface and both concrete implementations, embedding-model invocation for query-time embedding, reranker invocation.
- **Must not do:** know anything about incidents, postmortems, or agents — it only knows about documents, chunks, and queries. This keeps it reusable and independently testable.
- **Public interface:** `search(query, filters, top_k) -> list[ScoredChunk]`, `upsert(chunks) -> None`.
- **Can call:** `database` (for metadata joins), `shared`.
- **Cannot call:** anything else — it's a leaf module.

### `database/`

- **Responsibility:** the single shared persistence layer — SQLAlchemy models, Alembic migrations, session/connection management (Neon Postgres + pgvector extension).
- **Owns:** all table definitions. Every module's data lives here, but each module is only permitted to touch the tables it "owns" per the sections above — this is a code-review discipline, not something Python enforces mechanically, and is called out explicitly so it isn't lost.
- **Can call:** nothing (it's infrastructure, sits below everything).

### `shared/`

- **Responsibility:** cross-cutting concerns with no business meaning of their own — config loading, structured logging setup, base exception types, common Pydantic schemas (e.g., a shared `Citation` model used by both `agents` and `core`).
- **Can call:** nothing. Everything can call it.

---

## 4. Communication Design

### Inside the monolith today

- **Direct function/async-function calls** between modules' public interfaces. No HTTP, no serialization overhead, no network failure modes — but every call is written *as if* those constraints existed (plain-data arguments and return values, no shared mutable state, no passing live DB sessions across module boundaries).
- **Background jobs**, not separate services, for `ingestion` — implemented as async tasks on a job queue (Redis-backed, e.g. `arq`) running in-process or in a separate worker process from day one, since this workload is bursty enough to warrant its own process even before any other extraction happens. This is the one exception to "everything in one process": ingestion workers run as a distinct process from the API server from the start, communicating via the Redis queue — because the failure mode of blocking API request threads on a Slack API rate-limit is bad enough to design around immediately, and a queue is the same interface whether the worker is in-process-adjacent or a fully separate deployment.
- **Contracts**: every public interface function is fully typed (Pydantic models in, Pydantic models out). This typed contract *is* the future API contract — when a module is extracted, these Pydantic models become the request/response bodies.

### What changes if a module becomes an independent service

| Today (in-process) | After extraction |
|---|---|
| Direct async function call | HTTP call (FastAPI client) or message on a queue |
| Python exception propagation | HTTP error codes / retry-with-backoff |
| Shared transaction (same DB session) | No shared transaction — need explicit compensation/saga logic or eventual consistency |
| Implicit trust (same process) | Explicit auth between services (service-to-service token) |
| In-memory function contract (Pydantic models) | Same Pydantic models, now serialized as JSON over the wire |

Because the Pydantic contracts already exist and are already the *only* thing callers depend on, extraction is a matter of adding a thin transport adapter — not a rewrite.

---

## 5. Complete Data Flow

### Knowledge Ingestion Pipeline

```
Source (Slack/GitHub/Jira/Docs)
    ↓
Connector            — source-specific auth + fetch, normalizes to a raw Document
    ↓
Document Processing  — strip formatting noise, extract structured metadata (author, timestamp, source URL)
    ↓
Chunking             — split into retrieval-sized units, preserving source-anchored offsets for citation
    ↓
Embedding Generation — dense vector via sentence-transformers model
    ↓
Vector Storage       — upsert into Qdrant or pgvector (backend chosen per collection)
    ↓
Metadata Storage      — document/chunk metadata in Postgres, linked by chunk ID
    ↓
Indexing              — BM25/lexical index update alongside vector index, for hybrid search
```

Each stage is idempotent by design: a connector run is keyed by `(source, external_id, content_hash)`, so re-running ingestion on unchanged content is a no-op, and changed content produces a new version rather than a duplicate. Failures at any stage mark the job `failed` with the stage recorded, and retry resumes from that stage rather than re-fetching from the source.

### User Question Flow

```
User
    ↓
MCP tool call  OR  Core API request
    ↓
Agent Orchestrator (agents/) — entry point regardless of caller
    ↓
Retrieval Agent — query understanding, hybrid retrieval, rerank
    ↓
Confidence Evaluation — combine similarity/rerank/source-count signals into a score
    ↓
   ┌─────────────┴─────────────┐
High confidence          Low confidence
   ↓                             ↓
Answer Agent              Investigation Workflow
(cited response)          (evidence-gathering, hypothesis, human validation)
```

Both `mcp/` and `core/` are thin entry points into the *same* `agents.answer_question(...)` call — this is why agent logic must not live inside either of them: duplicating it would let the two entry points drift out of sync in confidence thresholds, prompt versions, or citation formatting.

### Unknown Incident Workflow (Knowledge Feedback Loop)

```
Unknown Incident (low confidence)
    ↓
Investigation Agent — search GitHub/PRs/commits/Slack/Jira/deployments/postmortems
    ↓
Hypothesis generated (root cause, evidence, confidence, suggested owner) — explicitly labeled as AI-generated, not fact
    ↓
Engineer resolves the incident (human action, outside the system)
    ↓
Postmortem Agent — draft postmortem generated from incident timeline + investigation evidence
    ↓
Human Review & Approval — engineer edits/approves the draft (mandatory gate)
    ↓
Knowledge Gap Agent — checks whether this incident type reveals a documentation gap
    ↓
Runbook created/updated (as a proposed draft, still human-gated)
    ↓
Chunk & Embed — same ingestion pipeline as any other document
    ↓
Vector Storage
    ↓
Available for future retrieval
```

The critical property of this loop: **nothing generated by an agent reaches the vector store without passing through the same human-review gate as any other authored document.** The Investigation Agent's hypotheses and the Postmortem Agent's drafts are stored as `proposed`, distinct from `published` knowledge, until a human approves them.

---

## 6. MCP Architecture

**MCP exists** to give any MCP-compatible AI client (Claude Desktop, Claude Code, future internal tools) a stable, permissioned way to use EKIP's capabilities without needing to know anything about its internal module structure, database schema, or agent implementation.

**MCP server responsibilities:** protocol handling, request validation, translating MCP tool calls into calls against `core`'s and `agents`' public interfaces, translating results back into MCP tool responses, and enforcing MCP-level auth scopes (which tools a given client/token is allowed to call).

**MCP contains no business logic.** This is worth restating because it's the most common way MCP integrations rot: a tool handler that "just quickly" queries the database directly instead of going through `core` is the beginning of a second, divergent business-logic path.

**Illustrative tools** (full contracts to be defined in `API_DESIGN.md`):
- `ask_question(query)` → routes to `agents.answer_question`
- `investigate_incident(incident_id)` → routes to `agents` investigation workflow
- `search_similar_incidents(description)` → routes to `agents`/`retrieval`
- `generate_postmortem(incident_id)` → routes to `agents.generate_postmortem`
- `propose_runbook_update(...)` → routes to `core`'s knowledge-proposal interface

**Resources:** read-only exposures such as a specific document or incident record, fetched through `core`'s interface.

**Prompts:** MCP-level prompt templates for common workflows (e.g., "triage this incident"), which are thin wrappers that call the above tools — not a separate prompt-engineering surface.

**Authentication boundary:** MCP clients authenticate with a token scoped to a user/service identity; `mcp/` resolves that identity and passes it into every `core`/`agents` call so downstream RBAC checks (owned by `core`) apply identically whether the request came from MCP or the REST API. MCP never has broader access than the REST API would grant the same identity.

---

## 7. Agent Architecture

LangGraph state machine, entry point `agents.answer_question` / `agents.triage_incident`:

- **Retrieval Agent** — query understanding and rewriting, hybrid retrieval (dense + BM25) against `retrieval/`, reranking, context assembly with citation anchors.
- **Confidence Evaluation Node** — not an LLM call; a scoring function combining vector similarity, reranker score, number of independent supporting sources, and (for incidents) historical similarity, into a single confidence value checked against a configurable threshold. Deterministic and unit-testable in isolation from any LLM.
- **Investigation Agent** — only reached on low confidence. Searches broader evidence sources (GitHub/PRs/commits/Slack/Jira/deployments — monitoring metadata mocked initially), correlates findings, and produces a hypothesis explicitly tagged with a confidence score and a clear verified-evidence-vs-AI-hypothesis distinction.
- **Answer Agent** — only reached on high confidence. Generates the final cited answer from the Retrieval Agent's context; forbidden from introducing claims not traceable to a retrieved chunk.
- **Postmortem Agent** — timeline reconstruction, root-cause extraction, action-item generation, structured report output — always produced as a draft pending human review.
- **Knowledge Gap Agent** — runs periodically (or after incident resolution) over incident/retrieval history to detect repeated low-confidence topics and recommend documentation to create or update.

**State:** a single typed graph state object threaded through all nodes, carrying the query/incident context, retrieved evidence, confidence score, and accumulated agent outputs — never a raw dict.

**Human approval points:** before any Investigation hypothesis or Postmortem draft is treated as "knowledge" (i.e., before it's eligible for embedding), and before any Knowledge Gap Agent recommendation becomes an actual runbook change.

**Failure handling:** each node distinguishes between a retryable failure (LLM timeout, transient API error — retried with backoff at the node level) and a terminal failure (e.g., no evidence found at all), which routes to a graceful "insufficient information" response rather than crashing the graph.

---

## 8. Database and Vector Architecture

### PostgreSQL / Neon (owned by `database/`, accessed per the module-ownership rules above)

Core tables: `users`, `roles`, `permissions`, `documents`, `document_metadata`, `incidents`, `incident_timeline`, `postmortems`, `audit_logs`, `agent_executions`, `mcp_requests`, `ingestion_jobs`. Full schema, relationships, and indexing strategy live in `DATABASE_DESIGN.md` (next document), kept in sync as the schema evolves.

### Vector storage: Qdrant vs. pgvector

Both are supported behind the same `retrieval.VectorStore` interface; the choice is per-collection, not global:

- **pgvector (in Neon)** — preferred default. Keeps vectors co-located with their metadata in the same transactional store, so metadata filtering is a normal SQL `WHERE` clause and there's no cross-store consistency concern between "the chunk exists" and "the chunk is searchable." Right choice for collections with moderate scale and where transactional consistency with the relational data (e.g., incidents, audit trail) matters more than raw ANN throughput.
- **Qdrant** — preferred when a collection needs higher-scale ANN performance, richer payload filtering at vector-search time, or independent scaling of the vector workload from the relational database (e.g., a documentation corpus that grows large and is queried far more often than it's written). Also the natural choice if/when `retrieval/` itself is extracted into its own service, since Qdrant is already a separate process.

The `VectorStore` interface (`search`, `upsert`, `delete`) is identical regardless of backend, so this choice is a per-collection configuration decision, not an architectural fork.

**Collections:** documentation, incidents, code, conversations — each with its own chunking strategy (e.g., code chunked by function/class boundary, not fixed token windows) and metadata schema, detailed in `DATABASE_DESIGN.md`.

**Hybrid search:** dense retrieval (embedding similarity) combined with BM25 (lexical) results, merged via reciprocal rank fusion, then passed through a reranker (cross-encoder) before the top-k reaches the Confidence Evaluation Node. This matters specifically because engineering queries often contain exact identifiers (error codes, function names, ticket IDs) that dense embeddings alone retrieve poorly.

---

## Open questions / not yet decided

- Exact confidence-score formula and threshold (to be tuned empirically once real retrieval data exists — placeholder logic first, documented in `ENGINEERING_DECISIONS.md` when finalized).
- Job queue library choice for `ingestion/` (`arq` vs. Celery) — leaning `arq` for its native asyncio support, not yet finalized.
- Whether `mcp/` needs multiple MCP servers (e.g., one for knowledge tools, one for admin tools) or a single server with scoped tools — leaning single server initially.

These will be resolved and logged in `ENGINEERING_DECISIONS.md` as we reach the relevant implementation phase.