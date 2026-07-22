# EKIP — Project Status

Status: **Living document. Update this at the end of every milestone.** This file exists so the project can be picked back up — in a new AI conversation, or by you alone — without re-deriving context from scratch.

Last updated: 2026-07-21

---

## Completed Work

**Documentation set (all six docs complete):**
- `docs/ARCHITECTURE.md` — full architecture document: project overview, modular-monolith decision, module design (`core`, `mcp`, `agents`, `ingestion`, `retrieval`, `database`, `shared`) with public interfaces and call boundaries, communication design, all three data flows (ingestion / question / unknown-incident feedback loop), MCP architecture, agent architecture, database & vector architecture.
- `docs/ENGINEERING_DECISIONS.md` — three entries recorded:
  - 001: modular monolith over microservices
  - 002: `ingestion/` runs as a separate worker process from day one (exception to single-process rule)
  - 003: `arq` chosen over Celery as the ingestion job queue library
  - Two open items remain (confidence-score formula/threshold, single-vs-multiple MCP servers).
- `docs/DATABASE_DESIGN.md` — full Postgres schema (users/roles/permissions, incidents, incident_timeline, postmortems, audit_logs, agent_executions, mcp_requests, ingestion_jobs, documents, document_metadata, per-collection chunk tables), ownership mapped to modules, indexing strategy, pgvector-vs-Qdrant per-collection rationale.
- `docs/API_DESIGN.md` — REST endpoints, internal module interfaces, and MCP tool contracts, all sharing one set of Pydantic models (`AskResponse`, `InvestigationResult`, `Postmortem`, etc.).
- `docs/AGENT_WORKFLOWS.md` — full LangGraph state schema, every node's steps/inputs/outputs/failure handling (Retrieval, Confidence Evaluation, Answer, Investigation with its two sub-stages, Postmortem, Knowledge Gap), transitions, failure handling policy.
- `docs/PROJECT_STRUCTURE.md` — full folder tree explained per-module, including the deliberate `app/agents/retrieval/` vs. `app/retrieval/` naming distinction.

**Project scaffold:**
- Full `app/` folder tree created on disk with `__init__.py` in every package directory — `core`, `agents` (with `retrieval`, `investigation`, `postmortem`, `knowledge_gap`), `mcp` (`servers`, `tools`, `resources`), `ingestion` (`connectors`, `processors`, `workers`), `retrieval` (`interfaces`, `qdrant`, `pgvector`, `ranking`), `database` (`models`, `migrations`), `shared` (`schemas`, `config`); plus `tests/{unit,integration}`, `deployment/`, `docker/`.
- `pyproject.toml` — dependencies pinned, tooling configured (ruff, mypy, pytest), and `import-linter` contracts encoding the module boundary rules from `ARCHITECTURE.md` §3 as an enforceable CI check.

**Implementation (Phase 1: Foundation, in progress):**
- `app/shared/config/settings.py` — environment-based `Settings` (Pydantic), covering database/redis/qdrant URLs, LLM API key, confidence threshold (default `0.6`, still tunable per the open item above), JWT config. Verified: loads correctly from env vars, validated end-to-end.

## Current Implementation Stage

**Phase 1: Foundation.** Documentation and project structure are done; we're now writing the shared foundation code that most other modules will depend on (config, then logging), before moving into `database/` and `core/`.

## Current File Being Worked On

Just finished: folding the `arq` decision into `ENGINEERING_DECISIONS.md` (#003) and this status update.
In progress / next up: `app/shared/config/logging.py` — structured logging (structlog) setup, the second shared foundation piece after settings.

## Pending Tasks

1. `app/shared/config/logging.py` — structured logging setup (in progress, was interrupted mid-creation — needs to be (re)written).
2. `app/database/models/` — SQLAlchemy models per `DATABASE_DESIGN.md`, one file per owning module's table group.
3. Alembic setup in `app/database/migrations/`.
4. `app/core/` implementation — auth, users, incidents, audit, per `API_DESIGN.md` §1–2.
5. Remaining Phase 1–7 work per the roadmap in the original spec, one file at a time.

## Next Recommended Step

Write `app/shared/config/logging.py` — it was the file being created when this status update was requested instead; still the right next step since settings.py and logging.py are the two things almost everything else depends on.

## Known Issues

- None in committed code. Note: an earlier attempt to create `logging.py` in this session hit a tool error before the file was written — it does not yet exist on disk and needs to be (re)created from scratch, not resumed.

## Important Context Required to Continue

- User wants strictly one file at a time — do not batch multiple docs or code files in a single turn.
- Architecture is a **modular monolith with extraction seams**, not microservices — see `ENGINEERING_DECISIONS.md` #001 before proposing any service split.
- `ingestion/` is the one module that runs as a separate worker process from day one, using `arq` — see `ENGINEERING_DECISIONS.md` #002 and #003.
- Module boundaries (`ARCHITECTURE.md` §3) are enforced both by convention and by `import-linter` contracts in `pyproject.toml` — keep new code consistent with those contracts or update them deliberately, not accidentally.
- User's existing stack: Python, FastAPI, PostgreSQL/Neon, pgvector, Docker, Redis, LangChain, LangGraph, Hugging Face/Sentence Transformers, Qdrant/ChromaDB/FAISS. New to MCP and production-scale system design — explanations should teach the reasoning, not just hand over code.
- Goal is a portfolio-quality, production-grade system, not a tutorial-style implementation.