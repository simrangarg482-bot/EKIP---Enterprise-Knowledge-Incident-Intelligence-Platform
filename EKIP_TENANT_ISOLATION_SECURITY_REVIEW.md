# EKIP Tenant Isolation Security Review

**Milestone 10 deliverable** (PROJECT_PLAN.md: "Security review pass specifically on tenant isolation (§3.7) — this deserves a dedicated review, not just code review as a byproduct of regular feature work, given the severity of a cross-tenant leak.")

**Scope.** This review checks the actual codebase, as it exists today, against every row of PROJECT_PLAN.md section 3.7's tenant-isolation enforcement table, plus section 12.3's restatement of the same defense-in-depth claim. It is a point-in-time review of a specific, named risk (cross-tenant data leakage), not a general code review — it does not cover authentication strength, injection risks, dependency vulnerabilities, or anything outside tenant isolation. Every claim below is backed by a specific file/function reference; nothing here is asserted from the docs alone without checking the code that's supposed to implement it.

**Verdict, up front:** six of the seven enforcement layers PROJECT_PLAN.md's table describes were genuinely implemented and held up under inspection at the time of this review. The seventh — **Postgres Row-Level Security, the database-level backstop** — did not exist at all when this review was first written. It has since been closed as a direct, immediate follow-up (see section 3 below, updated in place rather than left stale); everything else in this document reflects the original review pass.

---

## 1. Login — session token scoped to exactly one organization

**Claim:** "Session token is minted with exactly one `organization_id`; a user cannot request a token for an org they don't belong to."

**Checked:** `app/core/auth/service.py`. `_issue_access_token(user_id, organization_id)` (line 440) bakes `organization_id` into the JWT claims at signing time; the value comes from the already-resolved SSO login flow (`sso_config.organization_id`, the organization whose IdP the login request was verified against), never from a client-supplied field. `verify_access_token` (line 559) decodes the same claim back out. There is no code path where a client can request a token carrying a different `organization_id` than the one they authenticated against — the org is determined by *which IdP's login flow verified the user*, not by anything the client asserts.

**Verdict: Pass.**

## 2. Application queries — every repository function requires a tenant context

**Claim:** "Every repository function requires a tenant context parameter; no tenant-owned table is queryable without it."

**Checked:** all 11 `app/**/repository.py` files (`core/auth`, `core/users`, `core/audit`, `core/tenancy`, `core/incidents`, `core/knowledge`, `core/observability`, `agents`, `agents/knowledge_gap`, `ingestion`, plus `retrieval/pgvector/store.py`, which plays the same role for retrieval's tables). Every `select`/aggregate query against a genuinely tenant-owned table either takes `organization_id` directly and filters by it, or filters by a child key (`incident_id`, `document_id`, `connector_config_id`, `token_hash`) whose ownership was already resolved via an org-scoped lookup upstream.

**Nuance the doc's wording glosses over:** a number of functions are **PK-only lookups** — `get_organization_by_id`, `get_project_by_id`, `get_connector_config_by_id`, `get_incident_by_id`, `get_postmortem_by_id`, `get_document_by_id`, `get_invitation_by_id`, `get_access_rule_by_id`, `get_ingestion_job_by_id`, `get_refresh_token_by_hash`, and similar — that take no `organization_id` parameter at all and would return a row belonging to *any* organization if called with a UUID from a different tenant. The repository layer alone does not enforce isolation for these. Enforcement instead happens one layer up, in each module's `service.py`, via a consistent pattern: fetch by PK, then explicitly compare the returned row's `organization_id` against the caller's (`_ensure_same_organization` + a `_get_owned_*` helper, e.g. `core.tenancy.service`'s `deactivate_access_rule`/`revoke_invitation`, `core.incidents.service`'s `_get_owned_incident`/`_get_owned_postmortem`, `core.knowledge.service`'s `_get_owned_document`). This review directly verified that pattern in `core/tenancy/service.py`, `core/incidents/service.py`, and `core/knowledge/service.py` (all three read in full this session) — every mutating/reading function that resolves a resource by PK does perform the post-fetch ownership check before returning or acting on it. It was **not** independently re-verified line-by-line for `core/auth/service.py`'s refresh-token lookups or `core/users/service.py`'s user lookups in this pass; those are lower-risk (refresh tokens are looked up by an unguessable hash, not a client-suppliable UUID; user lookups are typically by the caller's own resolved identity) but are named here explicitly as **not exhaustively re-checked**, rather than silently assumed safe.

**Verdict: Pass, with one precision correction to the doc's own claim** (isolation is enforced at the service layer via a post-fetch ownership check, not literally "no tenant-owned table is queryable without" a tenant parameter at the repository layer) **and one disclosed gap in this review's own exhaustiveness** (auth/users lookups not individually re-traced).

**Documented, deliberate, already-justified exceptions** (not gaps): `core.tenancy.repository.list_organizations` (iterates every org by definition — the sole caller is the Knowledge Gap Agent's scheduled worker, which must enumerate every tenant); `core.tenancy.service.get_organization_sso_config`/`evaluate_provisioning` (run pre-login, before any `Identity` exists, resolved by slug/email instead); `ingestion.repository.list_active_connector_configs` (feeds the periodic reconciliation worker, which enqueues one job per connector_config — each resulting job is still org-scoped downstream). All three carry their own docstrings explaining exactly this.

## 3. Database — Postgres Row-Level Security as a hard backstop

**Claim:** "Postgres Row-Level Security policies as a hard backstop independent of application code" (§3.7); "defense in depth across three layers... Postgres Row-Level Security as a database-enforced backstop" (§12.3). Milestone 2's own scope explicitly lists "Postgres RLS policies for every tenant-owned table" as required work.

**Status: implemented as a follow-up to this review, immediately after it was written** (the user's own explicit prioritization: "close the RLS gap first"). What was actually built, in three migrations:

- `c7d4e8f19a2b_milestone_10_row_level_security.py` — `ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY` + a `tenant_isolation` policy (`USING (organization_id = current_setting('app.current_organization_id', true)::uuid)`, fail-closed via the `true`/missing_ok argument) on every table carrying `organization_id`, plus subquery-based policies for the two tables scoped only via a foreign key (`document_metadata`, `project_memberships`). `organizations`/`users`/`roles`/`permissions`/`role_permissions`/`mcp_requests` are deliberately excluded (see that migration's own docstring for why each).
- `d2e5f8a3c1b6_milestone_10_rls_bypass_functions.py` — four narrow `SECURITY DEFINER` SQL functions, each returning only an id (never a full row), for the small number of code paths that must discover a row's own `organization_id` *before* any `Identity`/org context exists to set the session GUC with: `resolve_connector_config_organization`, `resolve_document_organization`, `resolve_refresh_token_organization`, and `list_active_connector_config_ids` (a genuinely cross-tenant enumeration, not a single-row resolution).
- `app.database.session.set_tenant_context` — the actual `SET LOCAL`-equivalent (`set_config(name, value, true)`, parameterized rather than string-interpolated) every table's policy checks against, wired into every place `Identity`/org context becomes known: `app.api.deps.get_current_identity` (REST) and, via an injected-callable indirection preserving `app.mcp`'s "never imports app.database" contract, `app.mcp.dispatch.run_mcp_tool` (MCP); `app.core.tenancy.service.get_organization_sso_config`/`evaluate_provisioning` (pre-login, once each resolves an org by slug or receives one as a parameter); `app.agents.workers.tasks.run_knowledge_gap_detection_task` (already knows its org from its own arq job argument); and, via the bypass functions above, `app.ingestion.service._execute_ingestion_job`/`reindex` and `app.core.auth.service.refresh`/`logout` (all four of which start from a bare id/hash with no org context yet, discoverable only from the very row RLS would otherwise hide).

**A fifth, more severe ordering bug was found and fixed while implementing the above:** `app.core.users.service.resolve_identity` -- the function both `get_current_identity` and `resolve_mcp_identity` call to build an `Identity` -- itself queries `user_roles` (RLS-protected) to resolve roles/permissions, and it runs *before* either chokepoint gets a chance to call `set_tenant_context` (both originally called it only after `resolve_identity` already returned). Left as originally wired, this would not have raised an error: it would have silently resolved *every* login, on every request, to an `Identity` with empty `roles`/`permissions` (RLS hiding every `user_roles` row, not just cross-tenant ones), which fails every `authorize()`/`require_permission()` check closed -- effectively locking every user out of everything, invisibly, with no exception to point at. Fixed by moving `set_tenant_context` to the very first line of `resolve_identity` itself (it already receives `organization_id` as a required parameter, so no bypass function was needed here, unlike the four genuine bare-id cases above) -- the chokepoints' own calls are now an intentional, harmless redundancy. This is called out at this length because it is exactly the kind of subtle ordering mistake the "re-run this review after future milestones" recommendation below is meant to catch before it reaches this severity again.

**Remaining caveats, disclosed rather than assumed away:**

1. **Not yet verified against a live database.** This project's sandbox could not execute Python/SQL at any point this session (no disk space to start an isolated environment) — every piece of this (the RLS policies, the bypass functions, the session wiring) is unit-tested with fakes/monkeypatching, but has never been run against a real Postgres instance. Recommendation #2 below (confirming the app's DB role isn't a superuser/owner-bypass role) is *especially* unverified, since it requires an actual `psql` session against the deployed database.
2. **Manual/dev scripts that open a session directly** (e.g. any ad hoc `scripts/*.py` that calls `session_scope()` and queries an RLS-protected table without going through one of the chokepoints above) will now see zero rows, not an error — consistent with the fail-closed design, but a real behavior change worth knowing about before reaching for one of those scripts.
3. **`app.core.auth.service.revoke_all_sessions`** takes `organization_id` as a parameter but isn't wired to any REST/MCP endpoint yet (per its own docstring) — whichever future endpoint calls it will already have gone through `get_current_identity`/`resolve_mcp_identity` first, so it's covered by the chokepoint wiring once built, not before.

**Verdict: Closed.** The one real finding from this review's original pass now has a database-level backstop in place, tested at the unit level; the caveats above are the honest boundary of what "tested" means without a live database available this session.

## 4. Vector search — mandatory hard filter, never a post-filter

**Claim:** "`organization_id` (and `project_id` where relevant) is a mandatory filter passed into the retrieval query itself."

**Checked:** `retrieval/schemas.py`'s `SearchFilters` — `organization_id: uuid.UUID` is a required field (no default, cannot be omitted); `retrieval/pgvector/store.py` — both `search` (dense, line 92) and `lexical_search` (line 164) apply `.where(model.organization_id == filters.organization_id)` as a real SQL `WHERE` clause, before results are ever fetched — not a Python-side filter applied to an already-fetched result set. `retrieval/service.py`'s public `search()` takes `filters: SearchFilters` as a required (non-optional) parameter, so no caller can invoke retrieval without supplying one.

**Verdict: Pass.**

## 5. Background jobs — ingestion scoped per-connector_config, which is itself org-scoped

**Claim:** "Ingestion jobs are queued per-`connector_config`, which is itself organization-scoped; a job can never process or write data for an org other than the one that owns the connector."

**Checked:** `ingestion/service.py`'s `_execute_ingestion_job` — every job resolves its `organization_id` from `config_row.organization_id` (the `connector_configs` row's own column, fixed at registration time by `core.tenancy.service.register_connector`, itself gated by `_ensure_same_organization` + `tenancy:manage`), never from a client-suppliable parameter on the job itself. The constructed `Identity.for_agent("ingestion_worker", config_row.organization_id)` and every downstream write (`Document`, `document_metadata`, retrieval chunks) inherit that same value. There is no code path for a job to write data tagged with an organization other than the one its `connector_config` belongs to.

**Verdict: Pass.** (This review also added Milestone 10's rate-limiting and credential-encryption work to this same code path — see `app/ingestion/rate_limiter.py` and `app/shared/security/` — neither changes this scoping.)

## 6. Audit log — every row carries organization_id, tenant-scoped queries

**Claim:** "Every audit row carries `organization_id`; audit queries are tenant-scoped like everything else."

**Checked:** `core/audit/service.py`'s `record_audit_event` — `organization_id=actor.organization_id` is unconditional (not an `if` check, per the function's own docstring), so every audit row is tagged regardless of caller. `query_audit_log` requires both `actor` and `organization_id` and calls `_ensure_same_organization` before reading. The module's own docstring notes this scoping was *added* deliberately (Milestone 3) after a period where `query_audit_log` had no scoping at all and nothing called it yet — closed before any caller could rely on the unscoped behavior, per that docstring.

**Verdict: Pass.**

## 7. MCP — token-resolved org scope, no client-specified organization override

**Claim:** "The token resolved at MCP connection time carries the org scope; no MCP tool call can specify a different organization than the caller's own token."

**Checked:** `app/mcp/auth.py`'s `resolve_mcp_identity` resolves an `Identity` from the bearer token per-call (streamable-HTTP is stateless); every tool handler under `app/mcp/tools/` (spot-checked `ask_question.py`, consistent with the shared `run_mcp_tool` dispatch pattern every other tool handler uses) takes no `organization_id`/tenant parameter as a tool argument at all — the only identity a handler ever sees is the one `run_mcp_tool` resolved from the caller's own token, then passed straight into `agents.service`/`core.*.service` calls that are themselves subject to sections 1-6 above.

**Verdict: Pass.**

---

## Summary

| # | Layer | Verdict |
|---|---|---|
| 1 | Login token scoping | Pass |
| 2 | Application query scoping | Pass, with a documented precision correction + one disclosed re-verification gap (auth/users lookups) |
| 3 | Postgres RLS | **Closed as a follow-up** — see section 3 for what was built and what remains unverified against a live database |
| 4 | Vector search hard filter | Pass |
| 5 | Background job scoping | Pass |
| 6 | Audit log scoping | Pass |
| 7 | MCP token scoping | Pass |

## Recommendations, in priority order

1. ~~Design and implement Postgres RLS policies~~ **Done** (see section 3). Every table carrying `organization_id` now has `ENABLE`/`FORCE ROW LEVEL SECURITY` plus a fail-closed policy, and `set_tenant_context` is wired at every point `Identity`/org context becomes known, including the four bare-id/hash pre-Identity code paths that needed a narrow `SECURITY DEFINER` bypass function instead. **2026-08 update:** one more such gap was found and fixed during the "C2" audit pass — `core.tenancy.service.create_organization` inserted the auto-created default `projects` row *before* ever calling `set_tenant_context`, meaning that one write ran with the GUC unset (or stale) regardless of the role question in item 2 below. Fixed by moving the call to immediately after the organization row is created, mirroring `get_organization_sso_config`'s existing pattern; a regression test (`test_create_organization_sets_tenant_context_before_inserting_project`) asserts the ordering.
2. ~~Confirm the database role the application connects as is not a table-owner/superuser role~~ **Migration written, rollout still pending an operator action.** This project's `DATABASE_URL` connects as `neondb_owner` in every environment observed so far (Neon's default database-owner role) — `FORCE ROW LEVEL SECURITY` does not neutralize an owner role that also carries `BYPASSRLS`, and this codebase could not confirm from application code alone whether it does. `scripts/verify_rls_enforcement.py` (new) runs a live, read-only-plus-always-rolled-back-write behavioral check against the actually-configured connection to answer this conclusively; it could not be executed during this pass either (no live database access from this sandbox — the same recurring limitation as item 3 below), so **the finding remains empirically unconfirmed, though circumstantially very likely** (the connecting username itself, `neondb_owner`, is Neon's literal default owner-role name). Migration `f4a7c2e9b3d1_provision_rls_respecting_app_role` provisions a dedicated `ekip_app` role with `NOSUPERUSER NOBYPASSRLS` and exactly the grants the application needs. **This is not yet in effect**: an operator must (a) set `EKIP_APP_ROLE_PASSWORD` and run this migration against the real database, (b) run `scripts/verify_rls_enforcement.py` against the *current* `DATABASE_URL` first to confirm the finding, (c) update the deployed `DATABASE_URL` secret to connect as `ekip_app`, and (d) re-run the verification script to confirm the fix took effect. None of these four steps can be performed by an automated code change alone.
3. **Also still open: run the full RLS migration + bypass functions against a real Postgres instance at least once** (a staging environment, or even a local `docker-compose` Postgres) before treating this as production-ready — every piece of Milestone 10's RLS work was written and unit-tested without ever executing against a live database this session, per the recurring sandbox-unavailability disclosure throughout this milestone. `scripts/verify_rls_enforcement.py` (item 2 above) is the concrete tool to run once that access exists.
4. As a smaller, independent follow-up: extend this review's service-layer ownership-check verification to `core/auth/service.py` and `core/users/service.py`'s remaining PK-based lookups, closing the one disclosed gap in section 2 above.
5. Re-run this same review (or an automated equivalent — an import-linter-style or custom lint rule flagging any `repository.py` `select()` against a known tenant-owned model with no `organization_id` in its `.where()` clause) after any future milestone that adds new tenant-owned tables, so this doesn't have to be a fully manual re-audit every time. The same lint rule should also flag any new bare-PK/bare-hash lookup against an RLS-protected table that doesn't go through an already-established `set_tenant_context` chokepoint, so a fifth chicken-and-egg case (like the refresh-token one found and closed in this pass) doesn't go unnoticed in a future milestone.
