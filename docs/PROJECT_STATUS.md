# EKIP — Project Status

Status: **Living document. Update this at the end of every milestone.** This file exists so the project can be picked back up — in a new AI conversation, or by you alone — without re-deriving context from scratch.

Last updated: 2026-07-20

---

## Completed Work

- `docs/ARCHITECTURE.md` — full architecture document: project overview, modular-monolith decision, module design (`core`, `mcp`, `agents`, `ingestion`, `retrieval`, `database`, `shared`) with public interfaces and call boundaries, communication design, all three data flows (ingestion / question / unknown-incident feedback loop), MCP architecture, agent architecture, database & vector architecture.
- `docs/ENGINEERING_DECISIONS.md` — decisions log started. Two entries recorded:
  - 001: modular monolith over microservices
  - 002: `ingestion/` runs as a separate worker process from day one (exception to single-process rule)
  - Three open questions tracked (confidence formula, job queue library, single-vs-multiple MCP servers) — not yet decided, to be resolved and logged when we reach the relevant phase.

## Current Implementation Stage

**Documentation phase — no application code written yet.** We are still in the "before writing any code" phase of the process: architecture and continuity docs first, then project structure, then implementation.

## Current File Being Worked On

Just finished: `docs/ENGINEERING_DECISIONS.md`.
Up next (per the agreed doc sequence): `docs/DATABASE_DESIGN.md` and `docs/API_DESIGN.md`, then `docs/AGENT_WORKFLOWS.md`, then the full project folder structure — all before any implementation code.

## Pending Tasks

1. `docs/DATABASE_DESIGN.md` — schema, relationships, indexing strategy (referenced but not yet written; `ARCHITECTURE.md` §8 defers full detail here).
2. `docs/API_DESIGN.md` — API endpoints, request/response formats, internal interfaces, MCP tool contracts (`ARCHITECTURE.md` §6 defers tool contract detail here).
3. `docs/AGENT_WORKFLOWS.md` — LangGraph workflows, agent states/transitions, prompt strategies, evaluation logic in full detail (`ARCHITECTURE.md` §7 is the summary version).
4. Full production-level project folder structure, with every folder/file explained (per the original spec) — comes after the doc set above is complete.
5. Implementation, starting from Phase 1 (Foundation) per the original phased roadmap — one file at a time, after the structure exists.

## Next Recommended Step

Create `docs/DATABASE_DESIGN.md` next — it's a dependency for `API_DESIGN.md` (request/response shapes need to match the schema) and for the eventual `database/` module implementation.

## Known Issues

None yet — no code exists to have issues.

## Important Context Required to Continue

- User wants strictly one file at a time — do not batch multiple docs or code files in a single turn.
- Architecture is a **modular monolith with extraction seams**, not microservices — see `ENGINEERING_DECISIONS.md` #001 before proposing any service split.
- `ingestion/` is the one module that runs as a separate worker process from day one — see `ENGINEERING_DECISIONS.md` #002.
- User's existing stack: Python, FastAPI, PostgreSQL/Neon, pgvector, Docker, Redis, LangChain, LangGraph, Hugging Face/Sentence Transformers, Qdrant/ChromaDB/FAISS. New to MCP and production-scale system design — explanations should teach the reasoning, not just hand over code.
- Goal is a portfolio-quality, production-grade system, not a tutorial-style implementation.