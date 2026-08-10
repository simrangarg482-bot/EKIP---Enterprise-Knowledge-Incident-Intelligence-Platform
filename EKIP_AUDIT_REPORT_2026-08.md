# EKIP Comprehensive Codebase Audit

**Date:** 2026-08-08
**Scope:** Full codebase — multi-tenancy, auth/SSO, REST/MCP, ingestion/connectors, retrieval/RAG, agents/LangGraph, database/RLS, background jobs, test suite.
**Method:** Read-only. No code was modified, deleted, or rewritten as part of this audit. Findings come from direct reading of the current source (five parallel deep-read passes, one per subsystem, each explicitly instructed to hunt for bugs rather than describe architecture), cross-checked against real, empirical evidence already gathered earlier in this engagement (a real onboarding test run, and two real live-network connector test runs against Jira and Microsoft Teams). I attempted to execute the test suite myself (`pytest`) but the sandbox's shell was unavailable for the entire audit (`Not enough disk space to set up the workspace`) — this is an infrastructure limitation on my end, not a project issue. No test pass/fail counts in this report are fabricated; where I couldn't run something, I say so explicitly.

---

## Overall Verdict: **MAJOR ISSUES**

Not because the codebase is poorly built — it isn't. The architecture is coherent, unusually well-documented (nearly every module docstring explains *why*, not just *what*, and many honestly flag their own known gaps), and most subsystems checked out as correctly implemented on close reading. The verdict is **MAJOR ISSUES** rather than **NEEDS FIXES** because this audit found:

- A **confirmed, currently-exploitable cross-project data leak** inside the retrieval/RAG pipeline (Critical Issue #1) — the exact kind of multi-tenant isolation failure this audit was specifically asked to hunt for.
- Strong evidence that **Postgres Row-Level Security may be silently inert against the deployed database role** (Critical Issue #2) — which, if true, means every tenant-isolation guarantee documented for Milestone 10 is not actually enforced in the running system today.
- A **plaintext-secret storage bug** for every tenant's SSO client secret (Critical Issue #3).
- **Zero automated test coverage** of the entire RAG answer pipeline — the product's core feature — meaning bugs at this level of severity can and do ship undetected (this is not hypothetical: a real, production-breaking bug in the Jira connector was found only because a live-network test was built and run during this engagement, not by the existing suite).

None of these are exotic; all are readable in the code today. They are fixable — most have a clear, scoped fix below — but calling this "NEEDS FIXES" would understate that the two headline promises of this product (per-tenant and per-project data isolation, and a working RAG pipeline) both have confirmed, non-cosmetic breaks in them right now.

---

## What Is Correctly Implemented

- **Refresh-token rotation and reuse detection** (`app/core/auth/service.py`, `repository.py`): textbook-correct family-based rotation and reuse-triggered full-family revocation, not just claimed but verified against the actual repository calls.
- **ID-token algorithm selection** during SSO (`_exchange_code_for_claims`): the verification algorithm is read from the trusted JWKS document, never from the attacker-controllable JWT header — correctly avoids classic `alg` confusion attacks.
- **RLS tenant-context ordering** at every call site *except* one (see Critical #1): the previously-fixed `resolve_identity` ordering bug holds, and every "bare ID, no Identity yet" chicken-and-egg path (ingestion worker, token refresh/logout, MCP dispatch) correctly uses a narrow, non-leaking bypass-function-then-set-context-then-real-query sequence.
- **Session/transaction handling** (`get_db_session`, `session_scope`): commit/rollback/close is consistently correct; no leaked connections or partial commits found.
- **ORM-level multi-tenant modeling**: foreign keys, `NOT NULL`, and uniqueness constraints are consistently scoped per-organization/per-project where they should be (e.g., `(organization_id, name)` not a bare global unique on `name`).
- **Connector credential encryption**: universally correct across all 8 connectors — every credential goes through the same envelope-encrypt-before-persist path with no per-connector-type exception.
- **Confidence scoring and grounding logic**: the actual math (weighted renormalization, no division-by-zero, ungrounded-sentence removal, graceful all-dropped fallback) is implemented correctly — the *weights* are honestly flagged as untuned placeholders, but the mechanism itself is sound.
- **LangGraph wiring**: every node reachable, no dead ends, node exceptions correctly propagate rather than being silently swallowed by the graph runtime.
- **MCP/REST parity**: MCP tools defer permission enforcement to the same `core`/`agents` functions the REST routes call — no parallel, divergent authorization logic was found between the two transports.
- **Test quality where tests exist**: no skip/xfail hiding, no flaky time-based tests, and the service-layer tests that do exist generally assert on real transformation logic (filtering, permission checks) rather than just echoing a mock's return value. Pure-logic modules (clustering, envelope encryption, chunking) have genuinely good unit tests.
- **The Jira connector fix made during this engagement** (migrating off Atlassian's permanently-removed `/rest/api/2/search`) is internally correct and complete, aside from two follow-on issues it exposed (High #2, Medium #7 below).

---

## Critical Issues (must be fixed before production use)

### C1. Retrieval never enforces project-level permissions — confirmed cross-project data leak within an organization

**Confirmed bug.**

**WHAT:** `SearchFilters.project_ids` is defined and fully implemented in `PgVectorStore.search`/`lexical_search` (`app/retrieval/pgvector/store.py:100-101, 172-173`), but **no caller anywhere in the codebase ever populates it.** Every place that builds `SearchFilters` — `app/agents/retrieval/node.py:72-80`, `app/agents/investigation/evidence.py:163`, `app/agents/service.py:309,338`, `app/agents/knowledge_gap/pipeline.py:119` — passes only `organization_id` (and sometimes `permission_codes`), never `project_ids`.

**WHY this is worse than previously documented:** Every one of those call sites' docstrings, plus `app/shared/schemas/identity.py:24-31`, claims this is because *"`Identity.project_permissions` has no populated resolution path yet."* That is false today. `core/users/service.py:115-127` and `core/users/repository.py:166-206` (`get_project_permission_map`) show this is a real, working query, wired into `resolve_identity()` for every REST and MCP request. The data needed to fix this already exists and is simply never read by the retrieval layer.

**Real-world consequence:** A user who belongs only to Project A (not Project B) in the same organization **can** retrieve Project B's chunks via `ask_question` or the MCP `ask_question`/`search_similar_incidents`/`search_recent_changes` tools, whenever either (a) the target chunk's `acl_permission_code` is `NULL` (likely the common case for most ingested content), or (b) the user holds that ACL code via an org-level role grant, which is entirely independent of project membership in this schema. "Being in the org" is today functionally equivalent to "being in every project" for every search-backed feature.

**WHERE:** `app/agents/retrieval/node.py:72-80`, `app/agents/investigation/evidence.py:163`, `app/agents/service.py:309,338`, `app/agents/knowledge_gap/pipeline.py:119`, `app/shared/schemas/identity.py:24-31` (stale docstring).

**FIX:** In every `SearchFilters(...)` construction, resolve `project_ids` from `actor.project_permissions.keys()` and merge project-scoped permission codes into `permission_codes`, instead of leaving `project_ids=None` (all-projects) by default. This is a small, mechanical change at ~4 call sites, not a new data-layer build — correct the misleading docstrings at the same time so this doesn't get re-deprioritized as "blocked on unbuilt infrastructure."

---

### C2. Strong evidence that Postgres RLS is not actually enforced against the deployed database role

**Potential risk, but with direct empirical support — treat as effectively confirmed until disproven.**

**WHAT:** Milestone 10's entire RLS design depends on the app's Postgres connection role holding neither superuser nor `BYPASSRLS`. The project's own prior security review (`EKIP_TENANT_ISOLATION_SECURITY_REVIEW.md`, recommendation #2) states this was never verified. Separately, `create_organization` (`app/core/tenancy/service.py:141-146`) inserts the organization's default `projects` row **without ever calling `set_tenant_context` first** — and `projects` is RLS-protected with `FORCE ROW LEVEL SECURITY` and a bare `USING`/implicit `WITH CHECK` policy keyed on `app.current_organization_id`. Under real RLS enforcement, this insert should be rejected outright (the GUC can never legitimately equal the brand-new organization's just-generated ID).

**Empirical evidence from this engagement:** During the earlier real-world onboarding test (this same session), a live run of `create_organization` against the actual deployed database **committed the organization and its default project cleanly, with no RLS violation error.** Two explanations are possible, and only one is good news:
1. The deployed DB role bypasses RLS entirely (most likely explanation — consistent with Neon's common default of the connecting role being the database owner) — in which case **every RLS policy in the system is currently a no-op**, and every tenant-isolation claim resting on RLS as a backstop (not just this one insert) is not actually true in the running system.
2. Some other mechanism prevents the failure that isn't visible from the code read alone.

**WHY this matters more than C1 alone:** If explanation (1) is correct, RLS was never actually providing defense-in-depth at any point since Milestone 10 shipped — it is inert, not just incomplete. Every "RLS protects this" statement throughout the codebase's own documentation would need to be understood as "would protect this, if the role enforced it."

**WHERE:** `app/core/tenancy/service.py:141-146`; `app/database/migrations/versions/c7d4e8f19a2b_milestone_10_row_level_security.py:75-95,118-124`; `EKIP_TENANT_ISOLATION_SECURITY_REVIEW.md`.

**FIX:** Immediately verify the deployed role's attributes (`SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user;`) against the real database. If it does bypass RLS, provision a dedicated, non-owner, non-superuser application role with RLS-respecting grants and migrate the connection string to it — this is a prerequisite for RLS to mean anything at all. Separately, fix C1's missing `set_tenant_context` call regardless of the outcome, since it's a real gap either way.

---

### C3. SSO client secrets are stored and used in plaintext

**Confirmed bug.**

**WHAT:** `configure_sso` (`app/core/tenancy/service.py:245-288`) persists `data.client_secret_ref` via `insert_sso_configuration` with no call to `encrypt_secret`/`get_kms` — unlike its sibling `register_connector` in the same file, which does envelope-encrypt (`service.py:405`). `_resolve_client_secret` (`app/core/auth/service.py:239-253`) reads it back and uses it as-is, with no decrypt step. `SSOConfiguration.client_secret_ref: str` (`app/core/tenancy/schemas.py:182`) exposes it on the read schema too.

**WHY:** Every organization's OIDC client secret for their real IdP (Entra ID/Okta/Auth0/Google Workspace app registration) sits in the database as plaintext. A DB compromise, an overly-broad backup, or a bug elsewhere that surfaces raw rows leaks every tenant's IdP client secret — enough to impersonate EKIP to that tenant's identity provider.

**WHERE:** `app/core/auth/service.py:239-253`; `app/core/tenancy/service.py:271-279`; `app/core/tenancy/schemas.py:182`.

**FIX:** Wire `configure_sso` to call `encrypt_secret(get_kms(), data.client_secret_ref)` before persisting, exactly mirroring `register_connector`, and add the matching decrypt call to `_resolve_client_secret`. This is finishing an already-half-built pattern, not new infrastructure.

---

## High-Priority Issues

### H1. Ingestion job timeouts vanish without a trace; jobs are invisible while running

**Confirmed bug.** `_execute_ingestion_job` never commits the `ingestion_jobs` row until the *entire* sync (up to 30 minutes) finishes — only `session.flush()`. Two consequences: (a) `get_job_status` returns `NotFoundError` for any job that's still running, giving zero live visibility; (b) arq's `job_timeout` cancellation raises `asyncio.CancelledError`, a `BaseException` that none of the three relevant `except Exception` handlers (`app/ingestion/service.py:352`, `app/ingestion/workers/tasks.py:57`, `app/database/session.py:186`) catch — the whole transaction, including the job row itself, is discarded. A connector that reliably times out leaves **no record at all**, indistinguishable from a connector that has never run.
**Where:** `app/ingestion/service.py:262-267,352`; `app/ingestion/workers/tasks.py:57`; `app/database/session.py:182-190`.
**Fix:** Commit the `queued`→`running` transition in its own short transaction before the fetch loop starts, and/or wrap the fetch loop in `asyncio.wait_for` inside `_execute_ingestion_job` so a timeout becomes an ordinary, catchable, recordable failure rather than relying on arq's outer cancellation.

### H2. Today's Jira fix introduced an unthrottled per-issue HTTP burst

**Confirmed bug (introduced by the fix made earlier in this engagement).** The rate limiter acquires its token once per `fetch_batch()` call, not once per underlying HTTP request. Jira's fix (real, necessary, correctly implemented otherwise) now makes up to 101 real HTTP requests (1 search + up to 50 description fetches + up to 50 comment fetches) inside what the limiter still treats as "one request." At the configured 2.0 req/s budget, this will very likely trigger Jira's cost-based throttling on any project with real ticket volume.
**Where:** `app/ingestion/connectors/jira.py:229-235`; `app/ingestion/rate_limiter.py` (acquire-per-call design).
**Fix:** Either sharply lower Jira's page size, or move the rate-limiter `acquire()` call to fire once per real outbound `httpx` request inside `fetch_batch`, not once per method call — the module's own docstring already names this as the "correct, larger fix" for the general case; Jira's numbers just make it urgent for this specific connector.

### H3. Zero automated test coverage of the RAG answer pipeline; the one router test that touches it stubs the whole thing out

**Confirmed gap.** No test file exists anywhere under `tests/` for `app/agents/retrieval/*.py`, `app/agents/answer/*.py`, `app/agents/confidence.py`, or `app/agents/graph.py`. `tests/api/test_ask_router.py` — the closest thing to an end-to-end test of `/ask` — monkeypatches `agents_service.answer_question`/`triage_incident` entirely, so it never touches the real graph. Combined, there is no level (unit, service, or router) at which query-rewrite → hybrid search → rerank → confidence routing → generation → grounding → citations is ever exercised by `pytest`. This is not theoretical: the Jira 410 bug found and fixed earlier in this engagement was only caught because a real, live-network test was purpose-built and run — the existing suite would not have caught it, and would not catch an equivalent bug in the RAG pipeline either.
**Where:** absence across `tests/`; `tests/api/test_ask_router.py`.
**Fix:** Add real unit tests for `confidence.py` and `grounding.py` (both explicitly documented as pure/mockless-testable — this is low-hanging fruit), and at least one integration-style test that runs `answer_question` against a fake-but-real `VectorStore`/LLM double that exercises actual graph execution rather than stubbing the entry point.

### H4. No read-permission gate on incidents/postmortems — any org member can read everything

**Potential risk, architecturally real.** The permission catalog has no `incident:read`/`postmortem:read` code; `get_incident`, `list_incidents`, `get_timeline`, `get_postmortem` check only same-organization membership. A user provisioned with zero role assignments (a real, supported state per `resolve_identity`'s own docstring) can still read every incident and postmortem — including sensitive root-cause and evidence detail — in the org. By contrast, `observability:read` and `knowledge:review` *do* gate reads elsewhere in the same codebase, so the convention exists; incidents/postmortems are the one resource group missing it.
**Where:** `app/core/incidents/service.py:181-201,290-298,501-530`.
**Fix:** Either document this as a deliberate "all incidents are org-wide readable" decision, or add and wire a read-gating permission code consistent with the pattern already used for knowledge/observability.

### H5. Azure DevOps connector depends on a non-GA preview API — the same risk class that just broke Jira

**Potential risk.** `azure_devops.py:72` hardcodes `_COMMENTS_API_VERSION = "7.1-preview.3"`, acknowledged in its own docstring as preview-only, unlike every other endpoint that connector calls. Preview APIs carry no deprecation-notice guarantee — this is structurally the same exposure that just took down Jira's search endpoint.
**Where:** `app/ingestion/connectors/azure_devops.py:66-72,253-268`.
**Fix:** No code change strictly required today, but track this endpoint's changelog proactively, and make comment-fetch failures degrade (skip comments, keep the work item) rather than fail the whole item if/when this preview version changes shape.

### H6. Prompt injection via ingested content has no mitigation

**Potential risk.** Retrieved chunk content — sourced verbatim from Slack messages, GitHub issues/commits, etc. — is interpolated directly into LLM prompts (`app/agents/answer/generation.py:23-57`, `app/agents/investigation/hypothesis.py:47-118`, `app/agents/answer/grounding.py:115-128`) with no delimiter distinguishing trusted instructions from untrusted retrieved data, and no system/user message role separation. An attacker who can post to a connected Slack channel or open a GitHub issue controls text that later becomes part of these prompts verbatim. Blast radius is currently bounded (outputs are human-facing text/citations, not auto-executed actions), but the injection surface itself is real and unmitigated.
**Where:** as above.
**Fix:** Wrap retrieved content in explicit untrusted-data delimiters with an instruction that content inside is data, never instructions; use LangChain's `SystemMessage`/`HumanMessage` split instead of one flat string.

---

## Medium-Priority Issues

| # | Issue | Where | Fix |
|---|---|---|---|
| M1 | OIDC `state` is generated and required but never validated server-side; two docstrings contradict each other about whose job it is | `app/core/auth/service.py:199-236`; `app/api/routers/auth.py:53-60` | Add a server-side `state → code_verifier` store (Redis, already a dependency), or correct the docstrings if client-only validation is deliberate |
| M2 | `PermissionDeniedError`/403 used for authentication failures (missing/expired/invalid token, deactivated account), not just authorization failures — violates 401-vs-403 REST semantics, systemically | `app/core/exceptions.py:65-73`; `app/api/deps.py:57-71`; `app/core/auth/service.py:591-620` etc. | Introduce a distinct `AuthenticationError` (401 + `WWW-Authenticate`) for "no valid credential" cases; reserve 403 for "valid identity, insufficient permission" |
| M3 | No OIDC `nonce` parameter — ID-token replay isn't mitigated (PKCE covers code replay, not this) | `app/core/auth/service.py:104-161,256-346` | Generate/verify a `nonce` alongside `state`/`code_verifier` |
| M4 | IdP token-exchange/discovery `raise_for_status()` calls unwrapped — a routine expired/reused auth code becomes a raw 500 instead of a clean 4xx | `app/core/auth/service.py:181,288,299` | Wrap in try/except, re-raise as a typed error with a stable `error_code` |
| M5 | Tenancy admin *read* endpoints (invitations, access rules, connectors, projects, org) require only org membership, not `tenancy:manage` — any member can enumerate pending invite emails, provisioning rules, connector inventory | `app/core/tenancy/service.py:164-177,294-301,425-432,552-559,656-663` | Gate with `require_permission(actor, tenancy:manage)` to match the write-side convention |
| M6 | `GET /observability/mcp` aggregates stats across **every** organization on the platform, not the caller's own | `app/core/observability/service.py:56-83`; `app/api/routers/observability.py:45-51` | Add `organization_id` to `mcp_requests` and filter, or restrict to a platform-admin-only permission |
| M7 | `search_similar_incidents`/`search_recent_changes` MCP tools omit `permission_codes` from `SearchFilters` entirely — silently hides all ACL-gated content for every caller, inconsistent with the sibling retrieval path | `app/agents/service.py:309,338` | Pass `permission_codes=actor.permissions` matching `agents/retrieval/node.py`'s pattern |
| M8 | Jira's new pagination infers end-of-results from `nextPageToken` absence alone, ignoring the documented `isLast` field | `app/ingestion/connectors/jira.py:237-246` | Prefer `payload.get("isLast", ...)` as the authoritative signal |
| M9 | Azure DevOps/Confluence paginate by re-querying a live, mutable-order result set and slicing by position — items can be skipped or double-processed under concurrent writes during a long sync | `app/ingestion/connectors/azure_devops.py:177-214`; `app/ingestion/connectors/confluence.py:173-233` | Not urgent (content-hash idempotency limits damage to skipped items only), but worth disclosing as a known limitation the way GitHub's connector already does for itself |
| M10 | Rate limiter is in-process/in-memory only — multiple worker processes each enforce an independent budget, multiplying the real ceiling | `app/ingestion/rate_limiter.py` | Already disclosed in-code as follow-up work (Redis-backed bucket); no new action needed beyond confirming it's tracked |
| M11 | No aggregate observability for ingestion job health — no dashboard, and per H1, jobs can vanish silently with nothing to alert on | absence across `app/api/routers/observability.py`, `app/core/observability/*` | Add a `GET /observability/ingestion` endpoint aggregating by connector/source (failure rate, avg duration, stuck-job detection) |
| M12 | Stale docstrings claim `project_permissions` is unresolved (see C1) — actively misleads future readers into thinking the fix needs new infrastructure | `app/shared/schemas/identity.py:24-31`; `app/agents/retrieval/node.py:72-76`; `app/agents/investigation/evidence.py` | Correct the docstrings alongside the C1 fix |
| M13 | `python-dotenv` used by `tests/ingestion_retrieval/config.py` but never declared in `pyproject.toml` — a fresh venv following only declared dependencies would hit `SystemExit` during pytest collection | `tests/ingestion_retrieval/config.py`; `pyproject.toml` | Declare `python-dotenv` in `[project.optional-dependencies].dev`, or exclude `tests/ingestion_retrieval/` from default collection given it holds manual scripts, not pytest tests (see Testing Gaps) |
| M14 | No integration-level test ever runs against a real Postgres instance — zero coverage of the actual RLS policies, migrations, or repository SQL. This is *why* C1/C2 were never caught by CI | `tests/database/test_session.py` (explicitly uses a fake session); absence of any real-DB test anywhere | Add at least one CI job that runs Alembic migrations against a real (or testcontainers) Postgres and exercises a handful of RLS-sensitive writes end to end |
| M15 | No explicit connection-pool tuning (`pool_size`/`max_overflow`/`pool_recycle`); relies on SQLAlchemy defaults against Neon with no pooler/pgbouncer consideration despite Neon being explicitly discussed elsewhere in the same file | `app/database/session.py:88-101` | Tune explicitly once real concurrency/process-count targets are known; consider Neon's pooled connection string |

---

## Low-Priority / Code-Quality Issues

- Single global `asyncio.Lock` in the rate limiter serializes unrelated per-tenant/per-connector budgets — no correctness issue, just unnecessary contention under load. (`app/ingestion/rate_limiter.py:67-75`)
- LLM grounding's yes/no escalation call isn't independently retried — a transient failure there forces a full answer regeneration instead of just retrying the one cheap call. (`app/agents/answer/grounding.py:115-128`)
- No `conftest.py` anywhere in the repo — fixtures (`client`, `_actor()`, fake sessions) are duplicated near-identically across router test files.
- Inconsistent test-package structure: several `tests/` subdirectories are missing `__init__.py` while siblings have it — harmless today, fragile if module basenames ever collide.
- A stray informal comment in `app/shared/schemas/identity.py:122` is out of place in an otherwise formally-documented codebase.
- The shared-engine/event-loop-reuse bug (`RuntimeError: Event loop is closed`) found and fixed in this engagement's test harnesses is confirmed **not** present on any production code path (API, arq workers, MCP) — but the standalone scripts under `scripts/` that don't yet share the harness's fix would reintroduce it if ever changed to call `asyncio.run()` more than once per process.

---

## Security Concerns (consolidated)

1. **Real, live-looking secrets present in the project root `.env`** (DB password, Redis password, OpenAI key, JWT signing secret, Slack/GitHub/Jira/Confluence/Teams tokens) — confirmed present in the working tree during this audit. `.gitignore` does list `.env`, but this should be treated as a rotation trigger regardless: verify with `git log --all -- .env` whether it was ever committed, and rotate every credential in it as a precaution.
2. Critical #2 (RLS likely inert) and #3 (plaintext SSO secrets) above.
3. High #6 (prompt injection, unmitigated).
4. Medium #1, #3, #4 (SSO/OIDC completeness gaps: no server-side state validation, no nonce, unhandled IdP error responses).
5. Medium #2 (401-vs-403 conflation — a genuine, if lower-severity, security-adjacent API design flaw).
6. Medium #5, #6 (tenancy admin reads under-gated; cross-tenant observability aggregate leak).

---

## Architecture Concerns

- The overall module boundaries (`core/`, `ingestion/`, `retrieval/`, `agents/`, `mcp/`, `api/`, `database/`, `shared/`) are clean and consistently respected — no layering violations were found during this audit.
- The project's own documentation habit of naming its gaps in docstrings is a genuine strength, but Medium #12/C1 shows the failure mode of that habit: a docstring can become stale and actively mislead once the underlying assumption changes, and nothing currently re-verifies these claims. Worth periodically auditing "flagged gap" comments against current code, not just trusting them.
- The RLS-as-tenant-isolation-backstop design (Critical #2) is architecturally sound *if* the database role genuinely respects it — the design isn't the problem, the unverified assumption underneath it is.
- The retrieval layer's ACL model (`acl_permission_code` denormalized onto chunks) is a reasonable design, but Critical #1 shows it was only ever half-wired to the richer, project-scoped permission model that already exists elsewhere in the same codebase.

---

## Testing Gaps

- **Highest-impact gap:** zero pytest coverage of the RAG answer pipeline (High #3).
- **Second-highest-impact gap:** zero integration testing against a real Postgres instance, which is directly why Critical #1/#2 were never caught (Medium #14).
- `tests/ingestion_retrieval/` (built during this engagement) is a manual, credential-gated script suite, not CI-integrated pytest coverage — valuable for what it is (it's what caught the real Jira bug), but it doesn't run in CI and its `test_*.py` naming under `testpaths` creates a latent collection-time risk (Medium #13).
- Where tests do exist, they are generally well-written: real logic exercised (not vacuous mocks), no skip/xfail hiding, no flakiness found.

---

## Production-Readiness Assessment

**Not production-ready as-is**, specifically because of Critical #1 (confirmed cross-project leak) and Critical #2 (RLS likely inert) — these are not polish items, they are the core multi-tenancy promise not holding. Critical #3 (plaintext SSO secrets) is a straightforward but mandatory fix before onboarding any real customer's SSO. High #1 (silent job failures) and #3 (untested RAG pipeline) mean operational blind spots and undetected-regression risk respectively, both of which matter more, not less, once real traffic arrives.

The good news: every one of these has a scoped, describable fix (none require an architectural rewrite), and the codebase's documentation quality means the fixes can be made with high confidence about what else depends on the changed code.

---

## Missing / Incomplete Features

- Read-permission gating for incidents/postmortems (High #4).
- Ingestion job observability dashboard (Medium #11).
- Cross-process-coordinated rate limiting (Medium #10, already tracked in-code).
- Server-side OIDC `state`/`nonce` validation (Medium #1, #3).
- SSO client secret encryption (Critical #3).

---

## Findings by Classification

**1. Confirmed bugs:** C1, C3, H1, H2, M2, M6, M7, M8 (design gap), M13 (latent risk, confirmed absent-declaration).
**2. Potential risks (plausible, not fully provable from static reading alone, but well-evidenced):** C2 (strongly evidenced), H4, H5, H6, M9, M10.
**3. Missing features:** H4 (if unintentional), M6 fix direction, M11, M1/M3 (state/nonce), C3 fix (encryption wiring).
**4. Code-quality improvements:** M12, M15, rate-limiter lock granularity, grounding retry granularity, `conftest.py` consolidation, test-package `__init__.py` consistency, stray comment cleanup.
**5. Suggestions / optional improvements:** connection-pool tuning once scale targets are known, prompt-injection delimiter hardening as defense-in-depth even given today's bounded blast radius, periodic re-verification of "flagged gap" docstrings against current code.

---

## Appendix: What I Could Not Do

I could not execute `pytest`, run migrations against a live database, or make any real network/database call myself during this audit — the sandbox's Linux workspace failed to start (disk space) for every attempt. Every finding above comes from direct source reading, cross-checked internally for consistency (e.g., tracing every `set_tenant_context` call site by hand, not just grepping for its presence). Where this audit references empirical evidence (the RLS non-violation during org creation, the real Jira 410, the real Teams 401), that evidence came from actual command output pasted into this engagement earlier, not from assumption.
