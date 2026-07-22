# EKIP — Engineering Decisions Log

Status: **Living document.** Every non-trivial architectural or design decision gets an entry here at the time it's made, not retroactively. Entries are append-only — if a decision is later reversed, add a new entry that supersedes the old one rather than editing history.

Format per entry: Decision / Reason / Alternatives Considered / Tradeoffs / Date.

Last updated: 2026-07-21

---

## 001 — Modular monolith instead of microservices

**Decision:** Build EKIP as a single deployable application composed of strictly-bounded internal modules (`core`, `mcp`, `agents`, `ingestion`, `retrieval`, `database`, `shared`), rather than as independently deployed microservices from the start.

**Reason:** The project is built and operated by a solo developer. Microservices add operational surface area (service discovery, distributed tracing, network failure handling, versioned inter-service contracts) that teaches nothing about the platform's actual hard problems — retrieval quality, confidence scoring, agent orchestration. A monolith with disciplined internal boundaries (public interfaces only, no cross-module reach into internals, Pydantic-typed cross-module calls) preserves the ability to extract a module into its own service later, once there's real evidence — not speculation — that it needs independent scaling or ownership.

**Alternatives considered:**
- *Microservices from day one* — rejected: premature operational complexity with no current justification (no team boundaries to enforce, no proven differential scaling need).
- *Unstructured monolith* (no enforced module boundaries) — rejected: would be fast short-term but makes future extraction expensive, since everything short-term would probably end up coupled through shared DB sessions and imported internals.

**Tradeoffs accepted:**
- Module boundaries are enforced by code-review discipline and interface contracts, not by the OS/network — a careless import can silently violate a boundary in a way microservices would make impossible. Mitigation: every cross-module call is written as if it could become a network call tomorrow (plain-data Pydantic in/out, no shared sessions).
- Deferred, not eliminated, decisions about which module (if any) will eventually need independent scaling.

**Date:** 2026-07-20

---

## 002 — Ingestion runs as a separate worker process from day one (exception to "single process")

**Decision:** Unlike every other module, `ingestion/` runs as a distinct process from the API server from the start, communicating via a Redis-backed job queue — not as in-process function calls.

**Reason:** Ingestion workload (pulling from Slack/GitHub/Jira, chunking, embedding) is bursty, I/O-bound, and subject to external rate limits. If it ran in-process, a slow or rate-limited connector call could block request-serving threads/event-loop time for the transactional API — a failure mode severe enough to design around immediately rather than wait to discover in production. A queue-based worker is also the same interface whether the worker lives on the same host or a fully separate deployment later, so this doesn't compromise the "extract later" strategy — it just extracts this one piece slightly earlier than the others, for a concrete reason rather than a speculative one.

**Alternatives considered:**
- *Fully in-process (async background tasks within the API server)* — rejected: acceptable for very light ingestion load, but the explicit design goal of connecting many external sources (Slack, GitHub, Jira, docs) makes sustained load likely enough that the risk isn't worth taking on for a modest setup cost (a queue + one worker process).
- *Extract as a full independent service (own deployment pipeline, own scaling config) immediately* — rejected: no evidence yet that it needs independent *scaling*, only independent *process isolation*. A worker process sharing the same codebase/deployment artifact but running via a separate entrypoint gets the isolation benefit without the operational overhead of a truly separate service.

**Tradeoffs accepted:**
- Introduces a queue dependency (Redis) and worker-process lifecycle management earlier than strictly required by the "modular monolith" framing.
- Job status must be tracked explicitly (via `core`'s job-tracking interface) since the caller and worker no longer share a call stack or in-memory return value.

**Date:** 2026-07-20

---

## 003 — `arq` chosen as the job queue library for `ingestion/`

**Decision:** Use `arq` (Redis-backed, asyncio-native) as the job queue library backing the ingestion worker process defined in decision #002.

**Reason:** The rest of the stack (FastAPI, SQLAlchemy async engine, agent orchestration) is asyncio-first. `arq` is built directly on `asyncio`/`redis.asyncio`, so job handlers can `await` the same async DB sessions, HTTP clients, and embedding calls used everywhere else in the codebase without a sync/async boundary or a thread pool bridge. Celery's async support is a layer bolted onto a fundamentally sync/thread-based worker model, which would mean either writing ingestion connectors in a different style than the rest of the app, or paying a translation cost at every call.

**Alternatives considered:**
- *Celery* — rejected: mature and battle-tested, but its sync-first worker model fights the asyncio-native design used everywhere else in this codebase; would also pull in a heavier dependency (a message broker abstraction layer) for a queue need that Redis alone already satisfies, since Redis is already a dependency per decision #002.
- *Plain `asyncio` background tasks with a hand-rolled Redis queue* — rejected: `arq` already provides retry/backoff, job status tracking, and a worker CLI that a hand-rolled version would need to reimplement, for no real benefit over an existing, small, well-scoped library.

**Tradeoffs accepted:**
- `arq` is a smaller, less battle-tested project than Celery — less community tooling (e.g. no direct equivalent to Celery Flower for monitoring) if job-queue debugging needs grow more sophisticated later.
- Ties the ingestion worker's queue mechanics to Redis specifically (already true per decision #002, so this doesn't add a new dependency, just deepens reliance on the existing one).

**Date:** 2026-07-21

---

## Open — not yet decided (tracked here so they aren't silently forgotten)

- **Confidence-score formula and threshold** — will be decided empirically once real retrieval data exists; placeholder logic (default `0.6`, configurable via `Settings.confidence_threshold`) ships first.
- **Single MCP server vs. multiple (e.g., knowledge tools vs. admin tools)** — leaning single server initially; revisit if the tool count or permission model gets unwieldy.

Each will get its own numbered entry above once decided.