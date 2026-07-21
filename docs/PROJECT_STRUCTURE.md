# EKIP — Project Structure

Status: **Living document.** Update if folders are added, removed, or their responsibility changes. This is the physical realization of the module boundaries defined in `ARCHITECTURE.md` §3 — every folder below maps to a module, and the "can call / cannot call" rules from that section apply to imports between these folders exactly as described.

Last updated: 2026-07-20

```
ekip/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── PROJECT_STATUS.md
│   ├── ENGINEERING_DECISIONS.md
│   ├── DATABASE_DESIGN.md
│   ├── API_DESIGN.md
│   ├── AGENT_WORKFLOWS.md
│   └── PROJECT_STRUCTURE.md          # this file
│
├── app/
│   ├── core/
│   │   ├── auth/
│   │   ├── users/
│   │   ├── incidents/
│   │   └── audit/
│   │
│   ├── agents/
│   │   ├── retrieval/
│   │   ├── investigation/
│   │   ├── postmortem/
│   │   └── knowledge_gap/
│   │
│   ├── mcp/
│   │   ├── servers/
│   │   ├── tools/
│   │   └── resources/
│   │
│   ├── ingestion/
│   │   ├── connectors/
│   │   ├── processors/
│   │   └── workers/
│   │
│   ├── retrieval/
│   │   ├── interfaces/
│   │   ├── qdrant/
│   │   ├── pgvector/
│   │   └── ranking/
│   │
│   ├── database/
│   │   ├── models/
│   │   └── migrations/
│   │
│   └── shared/
│       ├── schemas/
│       └── config/
│
├── tests/
│   ├── unit/
│   └── integration/
│
├── deployment/
├── docker/
└── pyproject.toml
```

---

## A naming collision worth explaining up front

There are **two** things called "retrieval" in this tree, and that's intentional, not a mistake — but it's exactly the kind of thing that causes confusion later if it isn't written down:

- **`app/agents/retrieval/`** — the **Retrieval Agent**: a LangGraph node (§2.1 of `AGENT_WORKFLOWS.md`). It orchestrates query understanding, calls the retrieval library below, and assembles cited context. It contains agent logic and prompts.
- **`app/retrieval/`** — the **retrieval library**: the storage-agnostic `VectorStore` abstraction over Qdrant/pgvector (`ARCHITECTURE.md` §3, §8). It contains no agent logic, no prompts — just `search()`/`upsert()` and backend implementations.

The dependency direction is one-way: `agents/retrieval/` imports and calls `app/retrieval/`'s public interface. Never the reverse, and nothing in `app/retrieval/` should know an "agent" exists.

---

## `docs/`

Owned by: everyone (documentation, not code). No module boundary rules apply here — these are the project's continuity and design-of-record files, kept at the root so they're the first thing found when resuming work.

## `app/core/`

Owns the `users`, `roles`, `permissions`, `incidents`, `incident_timeline`, `postmortems`, `audit_logs` tables (per `DATABASE_DESIGN.md`) and the REST endpoints in `API_DESIGN.md` §1.

- **`auth/`** — login, token issuance/refresh, identity resolution (`Identity` object used everywhere else per `API_DESIGN.md` §2).
- **`users/`** — user/role/permission management, the `authorize()` function other modules call.
- **`incidents/`** — incident CRUD, timeline endpoints.
- **`audit/`** — `record_audit_event()` and the audit log query surface.

Each subfolder here will contain, once implementation starts: a `router.py` (FastAPI routes), `service.py` (business logic — the actual public interface), `repository.py` (DB access, the only place SQLAlchemy queries for this submodule's tables live), and `schemas.py` (Pydantic models specific to this submodule, re-exporting shared ones from `shared/schemas/` where applicable).

## `app/agents/`

Owns the LangGraph graph defined in `AGENT_WORKFLOWS.md`. No database tables of its own for domain data (writes go through `core/`'s interface) except `agent_executions`, which this module owns per `DATABASE_DESIGN.md`.

- **`retrieval/`** — Retrieval Agent node (see naming-collision note above).
- **`investigation/`** — Investigation Agent, its two sub-stages (evidence gathering, hypothesis generation) per `AGENT_WORKFLOWS.md` §2.4.
- **`postmortem/`** — Postmortem Agent, per §2.5.
- **`knowledge_gap/`** — Knowledge Gap Agent and its separate scheduled graph, per §2.6.

The graph definition itself (state schema, node wiring, the Confidence Evaluation node, the Answer Agent) doesn't map to any one of these subfolders — it lives at `app/agents/graph.py` (top-level within `agents/`), since it's the thing that *composes* the above, not a peer to them.

## `app/mcp/`

Owns nothing in the database except `mcp_requests`. Pure interface layer — every tool handler here is a thin translation into `core/` or `agents/` calls, per `ARCHITECTURE.md` §6.

- **`servers/`** — MCP server setup/registration.
- **`tools/`** — one handler per tool in `API_DESIGN.md` §3 (`ask_question`, `investigate_incident`, etc.).
- **`resources/`** — the `incident://` and `document://` resource handlers.

## `app/ingestion/`

Owns `ingestion_jobs`, `documents`, `document_metadata` (per `DATABASE_DESIGN.md`). Runs as a separate worker process per `ENGINEERING_DECISIONS.md` #002.

- **`connectors/`** — one module per source (`slack.py`, `github.py`, `jira.py`, `docs.py`), each implementing a common `Connector` protocol so adding a new source doesn't touch the pipeline.
- **`processors/`** — document cleaning, chunking — the middle stages of the pipeline in `ARCHITECTURE.md` §5.
- **`workers/`** — the queue consumer entrypoint (the actual separate process), job orchestration, retry/idempotency handling.

## `app/retrieval/`

Owns the `<collection>_chunks` tables for pgvector-backed collections (per `DATABASE_DESIGN.md`). A library — no HTTP surface, no agent logic (see naming-collision note above).

- **`interfaces/`** — the `VectorStore` protocol definition; the contract everything else in this folder implements.
- **`qdrant/`** — Qdrant-backed implementation.
- **`pgvector/`** — pgvector-backed implementation.
- **`ranking/`** — hybrid-search fusion (reciprocal rank fusion) and cross-encoder reranking — backend-agnostic, sits above both implementations.

## `app/database/`

Infrastructure layer — no business logic, per `ARCHITECTURE.md` §3.

- **`models/`** — SQLAlchemy models, one file per owning module's table group (e.g. `core_models.py`, `ingestion_models.py`) so ownership stays visible in the filesystem, not just in `DATABASE_DESIGN.md`.
- **`migrations/`** — Alembic migration scripts.

## `app/shared/`

Cross-cutting, no business meaning of its own, per `ARCHITECTURE.md` §3.

- **`schemas/`** — Pydantic models genuinely shared across modules (`Citation`, `Identity`, `EvidenceItem`, etc. — anything referenced from more than one module in `API_DESIGN.md`).
- **`config/`** — settings loading (environment variables, Neon connection string, Redis URL), structured logging setup.

## `tests/`

- **`unit/`** — mirrors the `app/` structure 1:1 (e.g. `tests/unit/agents/test_confidence_evaluation.py`), fast, no real DB/network — this is where the Confidence Evaluation node's deterministic logic (§2.2 of `AGENT_WORKFLOWS.md`) gets exhaustively tested with synthetic inputs.
- **`integration/`** — tests that hit a real (test) database and/or test vector store; fewer, slower, cover cross-module flows like "ask a question end-to-end."

## `deployment/`

Deployment configuration (not yet populated — will hold whatever the chosen deployment target needs: e.g. a `Dockerfile`-adjacent process manifest, environment templates). Deliberately left thin until Phase 7 (production hardening) in the roadmap, rather than speculatively filled now.

## `docker/`

Local development environment — `docker-compose.yml` for Postgres/Redis/Qdrant during development, separate from whatever `deployment/` eventually holds for a real target environment.

## `pyproject.toml`

Root-level — defines `app` as the installable package, pins dependencies, and configures tooling (ruff/mypy/pytest). This is the next file in the sequence, since it's what turns the folders above from plain directories into actual importable Python packages with enforced boundaries (import-linter or similar can use this to enforce the "cannot call" rules from `ARCHITECTURE.md` §3 automatically rather than relying on code review alone).

---

## What's physically in place right now

Every folder above has been created with an `__init__.py`, so `app` is already a valid (empty) Python package tree. No implementation code exists yet — that starts with `pyproject.toml`, then Phase 1 of the roadmap in `ARCHITECTURE.md`.