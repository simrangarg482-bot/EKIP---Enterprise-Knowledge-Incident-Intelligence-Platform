# EKIP — Engineering Decisions Log

Status: **Living document.** Every non-trivial architectural or design decision gets an entry here at the time it's made, not retroactively. Entries are append-only — if a decision is later reversed, add a new entry that supersedes the old one rather than editing history.

Format per entry: Decision / Reason / Alternatives Considered / Tradeoffs / Date.

Last updated: 2026-08-02

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

## 004 — `Identity` and RBAC resolution made organization-scoped (completes the PROJECT_PLAN.md §3.5-§3.6 migration)

**Decision:** `Identity` now requires an `organization_id`, and every role/permission resolution query in `core/users` is filtered by it. There is no code path left that resolves "this user's roles/permissions" without also specifying which organization they're being resolved within.

**Files changed:**
- `shared/schemas/identity.py` — `Identity.organization_id` added as a required field (no default); `Identity.project_permissions` added (see below); `has_permission()` extended to accept an optional `project_id`; `Identity.for_agent()` now also requires `organization_id`.
- `core/users/repository.py` — `get_role_names()` and `get_permission_codes()` both gained a required `organization_id` parameter and now filter on `UserRole.organization_id` in addition to `UserRole.user_id`.
- `core/users/service.py` — `resolve_identity()` and `get_user_profile()` both gained a required `organization_id` parameter, threaded into the repository calls; `authorize()` and `require_permission()` gained an optional `project_id` parameter.

**The previous problem:** `database/models/core_models.py` had already been migrated so that `UserRole`'s primary key is the composite `(user_id, organization_id, role_id)` — reflecting that the same person can hold different roles in different companies (PROJECT_PLAN.md §3.5). But `core/users/repository.py`'s `get_role_names()` and `get_permission_codes()` still queried only by `user_id`, and `Identity` carried no `organization_id` at all. The practical effect: a user's roles and permissions resolved across *every* organization they belonged to, not just the one their session was scoped to — a real cross-tenant authorization leak sitting dormant in already-committed code, not a hypothetical one. This was a known, called-out gap (PROJECT_PLAN.md §3.6 explicitly flags it as "a breaking change... needs a deliberate migration step, not a silent rewrite"), not a bug discovered by surprise.

**Reason `organization_id` is required on `Identity`, not optional:** PROJECT_PLAN.md §3.4 establishes that a session token is minted with exactly one `organization_id` claim, and §3.7 requires that claim to be the mandatory filter on every downstream query. Making the field optional on `Identity` would leave a code path where an org-less identity could be constructed and passed around — exactly the ambiguity that let the previous global-resolution bug exist in the first place. Requiring it turns "which organization is this identity scoped to" from a convention someone has to remember into something the type system enforces at construction time: `resolve_identity()` cannot return an `Identity` without one, and no other constructor for a user `Identity` exists.

**Alternatives considered:**
- *Keep `organization_id` optional on `Identity`, defaulting to `None`, and check for `None` at each call site* — rejected: this reintroduces exactly the same class of bug this migration fixes, just moved to "did every caller remember the `None` check" instead of "did every caller remember the `organization_id` filter." A required field with no default fails at construction instead of at a forgotten runtime check.
- *Scope organization filtering only at the repository layer, leave `Identity` unchanged* — rejected: `Identity` is the object threaded through every downstream `core`/`agents`/`mcp` call (API_DESIGN.md §2); if it doesn't carry `organization_id`, every one of those call sites would need to separately thread the organization through as a parallel argument, defeating the point of `Identity` being the single resolved-caller object.

**Tradeoffs accepted:**
- This is a breaking change to `resolve_identity()`, `get_user_profile()`, `get_role_names()`, `get_permission_codes()`, and `Identity.for_agent()` — every existing caller of any of these needed to start passing `organization_id`. Checked at the time of this change: no other module yet calls any of them (`core/auth` and `agents/` are still unimplemented stubs), so the breaking change landed with zero call sites to update — but it will constrain how `core/auth` must be written when it's built next (see impact below).
- `resolve_identity()` does not itself verify that a user is actually a *member* of `organization_id` — a user with zero role assignments there simply resolves to an `Identity` with empty `roles`/`permissions`, which fails closed on every subsequent `authorize()` check. Verifying actual org membership at login time (distinct from "member with zero granted permissions") is left to `core/auth`, not re-implemented here.

**Impact on the future authentication flow:** `core/auth` (PROJECT_PLAN.md §3.3, §9.1) does not exist yet, but this decision fixes its contract: after federating an employee's login via their company's IdP and minting a session token, `core/auth` must extract the `organization_id` claim from that token (or from the just-completed federation) before calling `resolve_identity(session, user_id, organization_id)` — it cannot call it with only a `user_id`. The same constraint applies to whatever resolves an MCP caller's identity per API_DESIGN.md §3 / PROJECT_PLAN.md §7.4: the MCP token's `organization_id` claim must be resolved and passed through identically to the REST path, which is exactly what keeps REST and MCP authorization behavior from drifting apart (ARCHITECTURE.md §6).

**`project_permissions` — added to the contract, not yet populated:** `Identity.project_permissions: dict[project_id, frozenset[str]]` was added in this same change per PROJECT_PLAN.md §3.6, and `has_permission()`/`authorize()`/`require_permission()` already accept an optional `project_id` and know how to check it. However, no repository or service code yet populates this mapping — there is no query joining `project_memberships` to resolve a user's project-scoped overrides. This is deliberately left as future work rather than bundled into this change: it touches a table (`project_memberships`) and module surface (`core/tenancy`, not yet built) beyond the scope of the organization-scoping fix, and the field defaulting to empty is safe (every project-scoped check simply falls back to the org-level set until the resolution logic is added).

**Date:** 2026-07-30

---

## 005 — SSO provisioning policy: domain rules, group rules, and invitations, replacing the "existing user = invited" stopgap

**Decision:** Introduce `organization_access_rules` (`rule_type` in `domain` |
`group`, each with a `grants_role_id`) and `invitations` (per-email, status-
tracked, time-boxed, also with a `grants_role_id`) as core/tenancy-owned
tables. `core/tenancy.evaluate_provisioning(organization_id, email, groups)`
is the single new function that decides whether a verified SSO login may
provision a user, checked in this precedence: (1) a pending, unexpired
invitation for the exact email, (2) an active domain rule matching the
email's domain, (3) an active group rule matching one of the IdP's `groups`
claim, (4) otherwise denied. `core/auth`'s `_resolve_or_provision_user` now
calls this, then `core/users.get_or_create_user`/`assign_role`, instead of
approximating "invited" as "a `users` row with this email already exists."

**Files changed:**
- `database/models/tenancy_models.py` -- added `OrganizationAccessRule`, `Invitation`.
- `core/tenancy/schemas.py` -- added `AccessRuleCreate`/`AccessRule`, `InvitationCreate`/`Invitation`, `ProvisioningDecision`.
- `core/tenancy/repository.py` -- persistence for both new tables.
- `core/tenancy/service.py` -- `create_access_rule`/`list_access_rules`/`deactivate_access_rule`, `create_invitation`/`list_invitations`/`revoke_invitation`/`accept_invitation`, and `evaluate_provisioning`.
- `core/users/repository.py` -- `insert_user`, `get_role_by_name`, `get_user_role`, `insert_user_role`.
- `core/users/service.py` -- `get_or_create_user`, `assign_role`.
- `core/auth/schemas.py` -- `VerifiedIdPClaims` (sub/email/name/groups).
- `core/auth/service.py` -- `_exchange_code_for_claims` now returns `VerifiedIdPClaims`; `_resolve_or_provision_user` rewritten around `evaluate_provisioning`.

**The previous problem:** `_resolve_or_provision_user` treated "a `users` row
with this email already exists" as proof of invitation -- documented in its
own docstring at the time as an explicit stopgap, not a design, since no
table existed yet to model real invitation/domain/group policy. This meant
provisioning had no actual product-configurable rule behind it: any org
could effectively be joined by anyone who already happened to have a global
`users` row with a matching email, regardless of that organization's actual
onboarding intent.

**Reason for the domain/invitation/group model, and why "email" is not a
third `rule_type`:** `PROJECT_PLAN.md`'s personas require both self-serve-
style onboarding (a small company that just wants "anyone `@ourcompany.com`
can join") and enterprise-style controlled onboarding (specific invited
people, or specific IdP groups like `"engineering"`). `organization_access_rules`
covers the two *coarse*, admin-configured, no-per-person-state cases (domain,
group); `invitations` covers the *fine-grained*, per-person case, and is
strictly more capable than a bare "allowed email" rule would be (status
lifecycle, expiry, who invited them) -- adding a third `rule_type` of
`"email"` would duplicate exactly what `invitations` already does, with less
capability, which is the over-engineering this design explicitly avoids.

**Alternatives considered:**
- *Keep a single `organization_access_rules` table with `rule_type` including `"email"`, no separate `invitations` table* -- rejected: loses invitation lifecycle (expiry, acceptance tracking, who invited whom) that a real enterprise onboarding flow needs, without saving meaningful schema complexity (one extra table).
- *Authorization logic inside `core/auth`* -- rejected outright per this migration's explicit constraint: `core/auth` verifies authentication and extracts claims; deciding who may join an organization is a policy question belonging to `core/tenancy`, the same separation already established between `core/users` (identity/roles) and everything that decides *whether* to grant them.

**Tradeoffs accepted:**
- A new `core/tenancy -> core/users` dependency (role-name-to-id resolution via `get_role_by_name`), not previously listed in `PROJECT_PLAN.md` section 9.2's dependency list (`database, shared` only). It is a read-only reference-data lookup against the global `roles` catalog, not a boundary violation in spirit, and is permitted by the existing import-linter contracts (no rule forbids core-submodule-to-core-submodule calls) -- but it is a real, new, documented fact about the architecture that section 9.2 should eventually be updated to reflect.
- No cleanup job sweeps expired-but-still-`"pending"` invitations; `evaluate_provisioning` lazily marks one `"expired"` only when it happens to be looked up past its `expires_at`. Acceptable for now (an admin can still see stale invitations via `list_invitations` and revoke them), but a periodic sweep would be a natural follow-up once ingestion's scheduled-job infrastructure exists.
- Bootstrapping the *first* user of a brand-new organization remains unsolved: `create_organization` seeds no initial access rule or invitation, so a fresh organization currently has no way to provision anyone via SSO until an admin (who doesn't exist yet) creates one. This is an onboarding-flow gap, not a provisioning-policy gap, and is left for whenever `core/auth`'s onboarding flow is designed.

**Date:** 2026-07-31

---

## 006 — Embedding model: `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions)

**Decision:** Use `sentence-transformers/all-MiniLM-L6-v2` as the embedding model backing Milestone 5's retrieval work, pinning the vector dimension at 384 for every pgvector `<collection>_chunks.embedding` column.

**Reason:** This resolves the two open items DATABASE_DESIGN.md's "Open items" section had flagged since the original single-tenant design ("Embedding dimension N — depends on final embedding model choice (not yet pinned)"). `all-MiniLM-L6-v2` is a small (~80MB), CPU-friendly, widely-used sentence-transformers model with no GPU requirement — consistent with decision #001's framing (a solo developer operating this, not a team with dedicated ML infrastructure). `sentence-transformers` itself was already a pinned core dependency (`pyproject.toml`) before this decision; only the specific model name and its dimension were undecided.

**Alternatives considered:**
- *A larger, higher-quality model (e.g. `bge-base-en-v1.5`, 768-dim, or an OpenAI embeddings API call)* — rejected for now: meaningfully better retrieval quality, but either a heavier local compute cost (larger model, slower CPU inference) or a new external API dependency and per-call cost, for a project with no real retrieval-quality data yet to justify the tradeoff. Revisitable once real query/retrieval data exists to benchmark against (same "decide empirically" posture already taken for the confidence-score threshold, in this document's "Open" section).
- *Leaving the dimension unpinned / deferring the model choice further* — rejected: pgvector requires a fixed `VECTOR(N)` column width per table, so Milestone 5's chunk tables cannot be created at all without picking a concrete dimension first; this was a blocking decision, not one that could be deferred past this milestone.

**Tradeoffs accepted:**
- 384 dimensions is on the smaller end for semantic search quality — acceptable for a first working version, not necessarily the final choice. Changing models later requires re-embedding every existing chunk and, if the dimension changes, a schema migration on every `<collection>_chunks` table (`VECTOR(N)` is fixed per column) — a real but bounded future cost, not a silent one.
- No GPU-accelerated inference path considered yet; embedding generation runs on CPU via `sentence-transformers`' default backend, which will need revisiting if ingestion volume grows large enough for embedding throughput to become a bottleneck.

**Date:** 2026-08-01

**Update (2026-08-13) — benchmarked against `BAAI/bge-base-en-v1.5`, no meaningful difference found:**
Per this entry's own "revisitable once real query/retrieval data exists to benchmark against," `scripts/eval_embedding_models.py` ran both models against the 36-question golden set (`scripts/eval_confidence_dataset.json`) over `test-org`'s real ingested corpus (93 chunks across documentation/code/conversations). Result: **identical** retrieval recall@5 (0.885 over the 26 questions with real evidence to retrieve) and **identical** clear-answer answer-grounding rate (0.857) for both models — full report in `scripts/eval_embedding_models_report.json`. The two models even missed the exact same three questions. This is a ceiling effect, not evidence the candidate has no advantage: a 93-chunk corpus is small enough that top-5 retrieval is close to saturated for both models — there simply isn't enough corpus size or query difficulty at this scale to separate a 384-dim and a 768-dim model. The comparison also surfaced that the current failure mode (a 0.25 grounded rate on `ambiguous` questions — i.e. the pipeline confidently answers 3 of 12 questions the corpus can't actually support) is identical for both models too, meaning it's an answer-generation/prompt issue, not a retrieval-ranking one, and won't be fixed by an embedding-model change either way. **No migration to `bge-base-en-v1.5` is warranted from this evidence** — re-run this benchmark if/when the real ingested corpus grows large enough that retrieval depth becomes a genuine bottleneck.

---

## 007 — Document-level ACL: a single optional permission-code gate, not a grant table

**Decision:** `documents.acl_permission_code: str | None` (nullable, default `NULL`). When set, `retrieval.search()`'s hard filter (PROJECT_PLAN.md §5.4's document-level ACL filter) additionally requires that permission code to be present in the caller's `Identity.permissions`, on top of the tenant and project filters. `NULL` means no additional restriction beyond tenant/project scope.

**Reason:** PROJECT_PLAN.md §5.4 requires a document-level ACL filter as a hard, non-negotiable retrieval constraint ("each document carries an ACL reference checked against the caller's permissions"), but no document anywhere defined that reference's concrete shape — no column, no table, no permission-code convention existed prior to this decision. Rather than invent a new authorization subsystem, this reuses the RBAC permission-code vocabulary that already exists (`incident:write`, `postmortem:approve`, `tenancy:manage`, ...) and already flows through `Identity.permissions` — a document restricted to, say, `hr:sensitive_read` is gated exactly the same way an incident-write action is, via one already-understood mechanism, rather than a second one.

**Alternatives considered:**
- *A real per-user/per-group grant table (`document_acl_grants`)* — rejected for this pass: strictly more expressive (arbitrary grantees, not just permission codes), but a meaningfully larger data model and query-complexity cost with no concrete use case yet driving the shape of "who specifically" needs document-level restriction beyond "holders of some permission code." Revisitable if a real requirement for per-person (not per-role) document grants emerges.
- *Defer document-level ACL entirely, tenant+project filtering only* — considered, but rejected: §5.5 frames the *absence* of document-level filtering as a live security gap the moment any document actually needs it (an HR-sensitive postmortem, for instance), and the one-column approach costs little enough to include now rather than bolt on under time pressure later.

**Tradeoffs accepted:**
- No ingestion connector or pipeline stage currently sets `acl_permission_code` to anything other than its `NULL` default — the column and retrieval's enforcement of it exist, but nothing populates it yet. In practice, no document is ACL-restricted until a future feature (e.g. connector-config-level tagging: "documents from this Slack channel are `hr:sensitive_read`-gated") sets it. This is a real, flagged gap, not a silent one: the enforcement mechanism is real and tested via `retrieval.search()`, but the "who decides which documents are restricted" question is unanswered.
- One permission code per document, not a set — a document needing multiple independent gating conditions (e.g. restricted to two unrelated permission codes, either sufficient) isn't expressible yet. Acceptable for a first pass; would need `acl_permission_code` to become an array column if that need arises.

**Date:** 2026-08-01

---

## 008 — LLM provider for agents/: OpenAI, not Anthropic

**Decision:** Every LLM-calling agent node (query rewriting, Answer Agent
generation, and eventually Investigation/Postmortem Agent LLM calls) goes
through `agents.llm.get_llm()`, backed by `langchain-openai`'s `ChatOpenAI`
and `Settings.openai_api_key`/`Settings.agent_llm_model` (default
`gpt-4o-mini`). `langchain-anthropic` -- pinned in `pyproject.toml` since the
project's initial scaffolding, before `agents/` had any real code -- is
removed; nothing in the codebase ever imported it.

**Reason this needed a real decision, not silent resolution:** `pyproject.toml`
had `langchain-anthropic` pinned, but `shared/config/settings.py` already had
`openai_api_key` (described in its own docstring as "used by all agent LLM
calls") -- two different providers implied by two different files, with
nothing in this log recording which one was actually intended. Beginning
Milestone 6 (the first milestone with real LLM-calling code) forced the
question, since `agents/llm.py`'s client construction can't serve both
silently. Asked directly rather than guessed, given the two documents
actively disagreed and guessing wrong would mean rewriting every node built
against the wrong SDK.

**Alternatives considered:**
- *Anthropic, matching the pinned dependency* — rejected (user's explicit
  choice): would have required renaming `openai_api_key` and reconciling
  `settings.py`'s own docstring, which already assumed OpenAI.
- *Support both, configurable per-deployment* — rejected for now: no
  concrete need for multi-provider support yet, and it roughly doubles the
  surface area (two SDKs, two credential shapes) `agents/llm.py` would need
  to abstract over for a solo-developer project (decision #001's framing)
  with only one provider actually in use.

**Tradeoffs accepted:**
- `Settings.agent_llm_model` defaults to `gpt-4o-mini` -- a reasonable,
  cost-conscious default (consistent with decision #006's "no dedicated ML
  infrastructure" framing), not an empirically-tuned choice; revisitable once
  real generation/grounding-quality data exists, the same "decide
  empirically" posture already taken for the confidence threshold.
- `langchain-anthropic` removed rather than left pinned-but-unused, since an
  unused, provider-mismatched dependency sitting in `pyproject.toml` is more
  likely to mislead a future reader than to help one.

**Date:** 2026-08-02

---

## 009 — Cross-encoder reranker model: `cross-encoder/ms-marco-MiniLM-L-6-v2`

**Decision:** Use `cross-encoder/ms-marco-MiniLM-L-6-v2` as the Retrieval
Agent's reranking model (PROJECT_PLAN.md section 5.3 / AGENT_WORKFLOWS.md
section 2.1 step 3), loaded via `sentence-transformers`' `CrossEncoder`.

**Reason:** Same posture as decision #006 (the embedding model choice): a
small (~80MB), CPU-friendly, widely-used model appropriate for a solo
developer with no dedicated ML infrastructure (decision #001), and no new
dependency -- `sentence-transformers` is already pinned and already provides
`CrossEncoder` alongside the `SentenceTransformer` class `retrieval/embedding.py`
uses. `ms-marco-MiniLM-L-6-v2` is a standard, widely-benchmarked choice
specifically trained for query-passage relevance ranking (MS MARCO), which
is exactly the reranking task here.

**Alternatives considered:**
- *A larger cross-encoder (e.g. `ms-marco-MiniLM-L-12-v2` or a bigger
  base model)* — rejected for now, same reasoning as #006: better quality,
  heavier CPU cost, no current data to justify the tradeoff over the
  smaller model. Revisitable once real query/rerank-quality data exists.
- *Skip reranking, ship RRF-fused results directly* — rejected: PROJECT_PLAN.md
  section 5.3 explicitly calls out reranking as part of Milestone 6's scope,
  and the two-stage recall-then-precision pattern is the documented design,
  not an optional enhancement.

**Tradeoffs accepted:**
- Adds a second CPU-bound model load (alongside the embedding model) to the
  agents process -- both run via `asyncio.to_thread` to avoid blocking the
  event loop, the same pattern `retrieval/embedding.py` already established,
  but this does mean two ~80MB models resident in memory rather than one.
- No GPU-accelerated inference path, same caveat as #006.

**Date:** 2026-08-02

---

## Open — not yet decided (tracked here so they aren't silently forgotten)

- **Confidence-score formula and threshold** — will be decided empirically once real retrieval data exists; placeholder logic (default `0.6`, configurable via `Settings.confidence_threshold`) ships first.
- **Single MCP server vs. multiple (e.g., knowledge tools vs. admin tools)** — leaning single server initially; revisit if the tool count or permission model gets unwieldy.

Each will get its own numbered entry above once decided.