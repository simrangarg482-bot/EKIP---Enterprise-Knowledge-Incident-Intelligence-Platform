# EKIP Code Reading Roadmap

A file-by-file sequence for building a complete mental model of EKIP (Enterprise Knowledge & Incident Intelligence Platform), ordered from foundations to leaves. Follow it top to bottom: **File 1 → File 2 → File 3 → ...**

## How to use this

Each entry gives you: the path, why you're reading it *at this point* (not earlier, not later), the concepts it teaches, what it leans on that you've already read, and what it unlocks. A few files (`agents/graph.py`, `agents/service.py`) are deliberately visited twice — once for their schema/signature shape, once in full — because their real bodies forward-reference modules you haven't read yet on the first pass. That's a normal, honest way to read a codebase with circular-feeling dependencies; don't fight it.

**Honest state of the project first:** a `tests/` directory now exists and is broad, if not total, in its coverage. Milestones 6-9's original suite (`tests/ingestion/connectors/test_github.py`, `tests/ingestion/processors/test_chunking.py`, `tests/agents/investigation/test_evidence.py`, `tests/agents/investigation/live/test_github_live.py`, `tests/agents/investigation/live/test_slack_live.py`, `tests/core/knowledge/`, `tests/api/`, `tests/agents/knowledge_gap/`) has since been joined by tests for Milestone 9's five remaining connectors plus the internal `runbooks` connector (`tests/ingestion/connectors/test_{jira,teams,azure_devops,confluence,sharepoint,runbooks}.py`), Milestone 10's production-hardening work (`tests/shared/security/`, `tests/ingestion/test_rate_limiter.py`, and RLS-ordering tests scattered across `tests/database/`, `tests/api/`, `tests/mcp/`, and several `tests/core/*/test_service.py` files), and the integration-gaps closure pass (new project-permission-pipeline tests in `tests/core/users/test_service.py`, project-scoped-connector-permission tests in `tests/core/tenancy/test_service.py`, new-endpoint tests in `tests/api/test_tenancy_router.py`/`test_users_router.py`/`test_auth_router.py`/`test_knowledge_router.py`, and a monitoring-connector-registration test in `tests/agents/investigation/test_evidence.py`).

Stages A-W below (Files 1-116) describe the codebase roughly as of Milestone 8/9 and are otherwise unchanged from those snapshots; **three stages have since been appended**, in the order the ground actually moved:

- **Stage X (Files 117-123, Milestone 9's five remaining connectors)** — Jira, Teams, Azure DevOps, Confluence, SharePoint, plus the internal `runbooks` connector, closing the documentation gap Stage W's own File 116 explicitly flagged as still open. All six are registered in `ingestion.service._CONNECTOR_REGISTRY` alongside Slack/GitHub.
- **Stage Y (Files 124-136, Milestone 10: production hardening)** — envelope encryption (AES-256-GCM) for connector credentials, per-connector/per-tenant ingestion rate limiting, a REST observability-dashboard read surface over `agent_executions`/`mcp_requests`, Postgres Row-Level Security (design, migration, RLS-bypass functions for the pre-tenant-context "chicken-and-egg" reads, and a full chokepoint-by-chokepoint wiring pass — including a real, previously-live ordering bug in `resolve_identity` found and fixed along the way), and the MCP SDK's confirmed port to the actually-installed `mcp==2.0.0` package (`MCPServer`/`Context`, resolving Stage S's own "unverified against the installed package" flag on File 91).
- **Stage Z (Files 137-146, the integration-gaps closure pass)** — a previously-missing REST/MCP surface for most of `core.tenancy.service` (organizations, projects, SSO configuration, access rules, invitations), a real project-scoped permission pipeline (`Identity.project_permissions`, populated end-to-end from JWT through to enforcement in incidents/knowledge/connector-registration), a "logout everywhere" session-revocation feature (self-service and admin-triggered), human-review-facing `GET`/`PATCH /knowledge/{document_id}` endpoints, and the `monitoring` live-evidence source (Stage O's File 77d, previously an inert, unregistered stub) finally wired into `agents.investigation.evidence._LIVE_SOURCES` and reachable.

The former "Stage X" (the decision log / project-status retrospective) is renumbered **Stage AA, Files 147-148**, to make room for all three. A FastAPI application object now exists (`app/api/main.py`) with a materially larger router surface than when it was first built (auth, incidents, ask, postmortems, knowledge, observability, tenancy, tenancy-admin, users); `app/mcp/tools/`, `app/mcp/resources/`, and `app/mcp/prompts/` are all populated, now with four more admin-surface tools than Stage V originally shipped. Three real, runnable process entrypoints exist: `scripts/run_mcp_server.py`, `app/ingestion/workers/main.py` (arq, plus its hourly `scheduled_reconciliation` cron job), and `app/agents/workers/main.py` (arq, the Knowledge Gap Agent's nightly scan). This roadmap reflects the code as it actually exists, not as the docs eventually intend it to be — a few docs (`PROJECT_STRUCTURE.md`, `PROJECT_STATUS.md`, and now `docs/ENGINEERING_DECISIONS.md`, which stops at decision #009 and has not been extended with Milestone 10 or integration-gaps entries despite both having plenty worth recording) are stale relative to the code for exactly this reason, and that's called out where it matters; `docs/USER_TESTING_GUIDE.md` (Stage Y, File 136) is the freshest whole-system narrative and should be trusted over either when they conflict.

---

## Stage A — Orientation & Configuration (Files 1–8)

You can't read module code sensibly until you know the rules it's obeying. This stage gives you the enforced architecture (import-linter contracts), the intended architecture (the docs), and how the app boots (settings, logging) — before a single business-logic file.

### 1. `pyproject.toml`
- **Read now because:** it's the ground truth for two things nothing else tells you as directly: the full dependency list (what stack this actually is — FastAPI/SQLAlchemy-async/asyncpg/alembic/pgvector, LangChain+LangGraph+OpenAI, MCP SDK, arq+Redis, structlog/OpenTelemetry) and the `[tool.importlinter]` contracts, which are the *enforced*, CI-checked version of the module-boundary rules every other file's docstrings keep citing.
- **You'll learn:** the tech stack; that `database` and `retrieval` and `mcp` each have hard "may not import X" rules; ruff/mypy/pytest configuration (strict mypy, `testpaths=["tests"]` even though that directory doesn't exist yet).
- **Depends on:** nothing.
- **Unlocks:** every later "per module docstring" reference to "the import-linter contract" — you'll recognize the exact rule being cited instead of taking it on faith.

### 2. `docs/Architecture.md`
- **Read now because:** this is the original architectural source of truth — module ownership, per-module can-call/cannot-call tables, the modular-monolith decision, three data-flow diagrams (ingestion, question-answering, unknown-incident feedback loop).
- **You'll learn:** why this is one deployable monolith with enforced internal boundaries rather than microservices; the intended shape of `core`/`mcp`/`agents`/`ingestion`/`retrieval`/`database`/`shared`; the three top-level data flows you'll trace in code later.
- **Depends on:** File 1 (you'll recognize the import-linter rules as this doc's tables, encoded).
- **Unlocks:** everything — this is your first mental map of the whole system.
- **Note:** inline "superseded" markers throughout mean anything about tenancy/RBAC/MCP-auth/schema specifics here has been overridden by `PROJECT_PLAN.md` (next file) — read it for the original shape, not the final one.

### 3. `docs/PROJECT_PLAN.md`
- **Read now because:** this is the actual, current, milestone-by-milestone plan — the document every piece of code you're about to read cites by section number (e.g. "section 6.4," "section 9.7"). It supersedes `Architecture.md` wherever they conflict.
- **You'll learn:** the full module dependency list per package (section 9.x — "agents/ may depend on retrieval, core, shared, not mcp/ingestion," etc.), the milestone sequence (1 through 9) that the whole project was built against, the target file tree, and the multi-tenancy model (organizations/projects/roles).
- **Depends on:** File 2 (you'll see exactly which parts of `Architecture.md` this revises).
- **Unlocks:** every subsequent module's docstring, essentially all of which cite a `PROJECT_PLAN.md` section number as their justification.

### 4. `docs/PROJECT_STRUCTURE.md`
- **Read now because:** it's the physical folder-tree explanation — worth a quick read specifically for the one genuinely confusing naming collision in this codebase: `app/agents/retrieval/` (the Retrieval *Agent*, a LangGraph node) versus `app/retrieval/` (the retrieval *library* it calls into). You will hit both paths repeatedly later and need to keep them straight from the start.
- **You'll learn:** the intended directory layout; the retrieval-agent-vs-retrieval-library distinction; an intended (not-yet-built) `tests/`, `deployment/`, `docker/` layout.
- **Depends on:** Files 2–3.
- **Unlocks:** painless navigation later — you won't second-guess which `retrieval` a given import means.
- **Note:** this doc is dated 2026-07-20, before most of the implementation existed — it still describes `core/` as only `auth/users/incidents/audit`, missing `tenancy` and `observability`, which you'll meet later. Treat it as a map sketched early, not a live index.

### 5. `alembic.ini`
- **Read now because:** it's short, and tells you where migrations physically live (`app/database/migrations`) and that the DB URL is a placeholder overridden at runtime — a detail that will make `database/migrations/env.py` (Stage C) make immediate sense instead of looking like dead config.
- **You'll learn:** migration script location; that this file is *not* the single source of truth for the DB URL despite appearances.
- **Depends on:** nothing.
- **Unlocks:** `app/database/migrations/base.py`/`env.py` (Stage C).

### 6. `.env`
- **Read now because:** you should know what categories of secrets this app expects before you read `settings.py`, which parses exactly these. Skim variable *names* only (`DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY`, `JWT_SECRET_KEY`, plus test-only Slack/GitHub credentials) — the values are live secrets, not something to study.
- **You'll learn:** which external services this app is wired to today (Neon Postgres, a Redis Cloud instance, OpenAI) versus which it isn't yet (no Jira/Azure DevOps/Teams credentials exist — matching the Investigation Agent's documented gap you'll meet much later).
- **Depends on:** nothing.
- **Unlocks:** File 7.

### 7. `app/shared/config/settings.py`
- **Read now because:** this is the one object nearly every other module reads from (`get_settings()`), so its shape needs to be fixed in your head before anything else.
- **You'll learn:** `Settings(BaseSettings)`'s fields — `database_url`, `redis_url`, `default_vector_backend` (pgvector vs. qdrant), `openai_api_key`, `agent_llm_model` (default `gpt-4o-mini`), `confidence_threshold` (default 0.6), JWT secret/algorithm/expiry, and two later additions for the Investigation Agent's live-evidence extension (Files 77-77d): `investigation_live_evidence_enabled` (a kill-switch, default `True`) and `investigation_live_evidence_lookback_hours` (default 24, how far back a live source searches); the `@lru_cache`-wrapped singleton pattern (`get_settings()`) you'll see reused everywhere.
- **Depends on:** File 6 conceptually (what it parses).
- **Unlocks:** essentially every later file that calls `get_settings()`.
- **Note a real, minor inconsistency here:** `Settings` defines an unused `REFRESH_TOKEN_EXPIRY_DAYS` ClassVar; the actual refresh-token lifetime is hardcoded separately in `core/auth/service.py` (Stage F). Small, worth remembering when you get there.

### 8. `app/shared/config/logging.py`
- **Read now because:** `get_logger(name)` is the one sanctioned logging entrypoint you'll see imported at the top of nearly every service/repository file from here on — better to know what it does once than wonder about it 80 times.
- **You'll learn:** `configure_logging()`'s structlog wiring (JSON in production, colored console otherwise) and that SQLAlchemy/httpx loggers are deliberately quieted.
- **Depends on:** File 7 (`get_settings()`).
- **Unlocks:** the `logger = get_logger(__name__)` line you'll now recognize instantly in every subsequent file.

---

## Stage B — Load-Bearing Shared Abstractions (Files 9–11)

Three tiny files, disproportionately important: nearly every function signature you'll read from here on takes an `Identity` and can raise an `EKIPError` subclass. Learn these cold before anything else.

### 9. `app/shared/schemas/identity.py`
- **Read now because:** `Identity` is threaded through *every* `core/` and `agents/` public call in this codebase — it's the single most load-bearing type here, and it's transport-agnostic (no JWT/HTTP knowledge), so you can understand it fully in isolation.
- **You'll learn:** `ActorKind` (USER/SERVICE/AGENT), `Identity`'s frozen shape (`organization_id` required — no org-less identity exists), `permissions`/`project_permissions` (org-level vs. project-scoped override), `audit_tag` (the `"user:<id>"`/`"agent:postmortem_agent"` string format written into every timeline/audit row), and `Identity.for_agent(agent_name, organization_id)` — the constructor used whenever an internal process (not a human) needs to act with its own identity.
- **Depends on:** nothing.
- **Unlocks:** literally every module from Stage D onward — you'll see `actor: Identity` in nearly every function signature you read.

### 10. `app/shared/schemas/common.py`
- **Read now because:** it's small, has zero dependencies, and defines a handful of `Literal` type aliases (`ActionItemStatus`, `AgentExecutionStatus`, etc.) referenced from several unrelated modules later — better to have seen them once already.
- **You'll learn:** the shared vocabulary types that don't belong to any one module.
- **Depends on:** nothing.
- **Unlocks:** reading schema files later without stopping to look up what `ActionItemStatus` means.

### 11. `app/core/exceptions.py`
- **Read now because:** `EKIPError` is the base class for every *expected* domain error (bad input, not-found, permission-denied) — and its docstring makes an explicit, important distinction you'll see enforced repeatedly: `EKIPError` subclasses are *not* for unexpected failures, which should propagate as ordinary exceptions instead. That two-tier distinction shapes failure handling in `core/` and especially `agents/service.py` much later.
- **You'll learn:** the `EKIPError` hierarchy (`NotFoundError`, `PermissionDeniedError`, `ValidationError`, `ConflictError`, etc.) and each one's `error_code`/`status_hint`.
- **Depends on:** nothing.
- **Unlocks:** every `service.py` file from Stage D onward, all of which raise these; and `agents/service.py`'s two-tier failure handling (Stage Q).

---

## Stage C — Database Schema Layer (Files 12–25)

Now the physical data model, bottom-up: the engine/session plumbing, then every table (in dependency order — tenants before the things scoped to tenants), then the migrations that actually create them, then the narrative doc tying it together.

### 12. `app/database/session.py`
- **Read now because:** every repository function you'll read from Stage D onward takes an `AsyncSession` — you need to know where it comes from and who owns commit/rollback before reading fifty functions that receive one as a parameter.
- **You'll learn:** `Base` (the single shared `DeclarativeBase` every model inherits), the async engine setup (`_normalize_database_url` stripping libpq-only params Neon's asyncpg driver rejects), `get_db_session()` (the FastAPI-dependency-shaped generator — commit/rollback/close), and `session_scope()` (the equivalent for non-request contexts like workers and scripts).
- **Depends on:** File 7 (`get_settings().database_url`).
- **Unlocks:** the "one shared session per call, never opened internally by a repository" convention you'll see honored everywhere.

### 13. `app/database/models/__init__.py`
- **Read now because:** it's a 30-second read that states the one-file-per-owning-module convention before you see it in practice across six files.
- **You'll learn:** the convention itself.
- **Depends on:** nothing new.
- **Unlocks:** Files 14–20 reading as a coherent set rather than an arbitrary file split.

### 14. `app/database/models/tenancy_models.py`
- **Read now because:** `Organization` is the tenant root every other table eventually scopes to — read it before anything that references `organization_id`.
- **You'll learn:** `Organization`, `Project` (with `is_default` for the auto-created "General" project), `SSOConfiguration`, `ExternalIdentityMapping` (resolves IdP subject → user, per org — written by `core/auth` despite living here), `ConnectorConfig` (source/credential-ref/config JSONB per org+project), `ProjectMembership` (project-level RBAC), `OrganizationAccessRule` (domain/group auto-join), `Invitation`. All FKs use plain string table-name references specifically to avoid circular imports with `core_models.py` — notice that pattern now, you'll see it again.
- **Depends on:** File 12 (`Base`).
- **Unlocks:** Stage D (`core/tenancy`) directly; also `core/auth` (Stage F), which writes `ExternalIdentityMapping` despite it living in this file.

### 15. `app/database/models/core_models.py`
- **Read now because:** this single file holds `User`/`Role`/`Permission`/`RolePermission`/`UserRole` (core/users), `AuditLog` (core/audit), and `Incident`/`IncidentTimeline`/`Postmortem` (core/incidents) all together — a real architectural artifact worth noticing: this file predates the later split into `tenancy_models.py`/`auth_models.py`, and nobody has gone back to split it further. Reading it now means you never have to context-switch back to it three separate times later.
- **You'll learn:** the full users/RBAC/audit/incidents table shapes in one pass.
- **Depends on:** File 14 (FKs to `organizations`/`projects`).
- **Unlocks:** Stages E, G, H (`core/users`, `core/audit`, `core/incidents`) all at once.

### 16. `app/database/models/auth_models.py`
- **Read now because:** `RefreshToken` is small and self-contained, and you now have `User`/`Organization` in your head to understand its FKs.
- **You'll learn:** `RefreshToken`'s `family_id` (groups tokens descended from one login, for reuse-detection/mass-revocation) and `token_hash` (SHA-256, never plaintext).
- **Depends on:** Files 14–15.
- **Unlocks:** Stage F (`core/auth`)'s refresh/rotation logic.

### 17. `app/database/models/ingestion_models.py`
- **Read now because:** `IngestionJob`/`Document`/`DocumentMetadata` are next in dependency order (they reference orgs/projects/connectors).
- **You'll learn:** `IngestionJob` (delegates its source info to `connector_configs` rather than duplicating it), `Document` (org+project scoped, content-hash-deduplicated, `acl_permission_code` for document-level ACL — ENGINEERING_DECISIONS.md #007), `DocumentMetadata` (EAV key/value).
- **Depends on:** File 14.
- **Unlocks:** Stage J (`ingestion/`).

### 18. `app/database/models/retrieval_models.py`
- **Read now because:** this is where the vector search infrastructure actually lives at the schema level, and it's worth understanding before you read any retrieval code.
- **You'll learn:** the shared `_ChunkColumns` mixin and the three concrete pgvector-backed tables (`DocumentationChunk`/`CodeChunk`/`ConversationChunk`) — `embedding: Vector(384)` (dimension pinned per ENGINEERING_DECISIONS.md #006), and `content_tsv` (a `GENERATED ALWAYS AS to_tsvector(...) STORED` column, GIN-indexed — the lexical half of hybrid search, honestly documented as Postgres full-text search rather than literal BM25). Note there's no "incidents" collection yet — a flagged gap you'll meet again in the Investigation Agent.
- **Depends on:** File 14, and requires Postgres's `vector` extension.
- **Unlocks:** Stage I (`retrieval/`) entirely.

### 19. `app/database/models/agent_models.py`
- **Read now because:** `AgentExecution` is the one table `agents/` owns — small, and a good preview of what agent observability looks like before you read any agent code.
- **You'll learn:** `AgentExecution`'s shape (`agent_name`/`trigger_source`/`input_summary` JSONB/`confidence_score`/`status`) — the running→succeeded/failed lifecycle you'll see implemented in `agents/service.py` much later, and its role as the future Knowledge Gap Agent's data source.
- **Depends on:** File 14.
- **Unlocks:** Stage K (`agents/` foundation) and specifically `agents/repository.py`.

### 20. `app/database/models/mcp_models.py`
- **Read now because:** last of the model files, and it teaches a specific, deliberate dependency-inversion trick worth understanding before you reach `app/mcp/` much later: `McpRequest`'s docstring explains *why* its write access lives in `core/observability`, not `app.mcp` — the import-linter contract forbids `mcp` from importing `database` at all.
- **You'll learn:** `McpRequest` (`tool_name`/`identity`/`request_summary`/`status_code`/`latency_ms`) and the ownership-inversion pattern.
- **Depends on:** File 14.
- **Unlocks:** Stage R (`core/observability`) and Stage S (`mcp/`) much later — you'll already know *why* observability is split out the way it is.

### 21. `app/database/migrations/base.py`
- **Read now because:** this is the *real* Alembic environment (the one that actually runs), and it makes a satisfying "oh, that's why" moment: it imports every model module you just read, purely so their tables register on `Base.metadata` before autogenerate.
- **You'll learn:** how `sqlalchemy.url` gets overridden from `Settings` at runtime (tying back to File 5's placeholder); the async migration machinery (`run_migrations_online` via `AsyncEngine.connect().run_sync(...)`).
- **Depends on:** Files 5, 7, 12, 14–20.
- **Unlocks:** File 22.

### 22. `app/database/migrations/env.py`
- **Read now because:** it's one line (`from app.database.migrations import base`) — read it right after `base.py` so you understand *why* Alembic's conventional entrypoint filename just re-exports someone else's side effects.
- **You'll learn:** nothing new conceptually; confirms the wiring from File 21.
- **Depends on:** File 21.
- **Unlocks:** nothing further; closes the loop.

### 23. `app/database/migrations/versions/be0234931e65_initial_schema.py`
- **Read now because:** this is the base schema migration — seeing the *actual* `CREATE TABLE` operations for organizations/users/roles/incidents/etc. in one file cements everything from Files 14–15 as real, not just Python classes.
- **You'll learn:** the complete first-cut schema: organizations, permissions, roles, users, audit_logs, external_identity_mappings, invitations, organization_access_rules, projects, refresh_tokens, role_permissions, sso_configurations, user_roles, connector_configs, documents, incidents, project_memberships, document_metadata, incident_timeline, ingestion_jobs, postmortems.
- **Depends on:** Files 14–17.
- **Unlocks:** File 24 (you'll immediately see what changed since).

### 24. `app/database/migrations/versions/f8698cb5abae_milestone_5_and_6_retrieval_ingestion_.py`
- **Read now because:** the second, additive migration — read right after the first so you see the diff between "what Milestones 1–4 needed" and "what retrieval/ingestion added."
- **You'll learn:** `CREATE EXTENSION IF NOT EXISTS vector;`, the three pgvector chunk tables plus their GIN tsvector indexes, `agent_executions`, and the additive `documents.acl_permission_code` column.
- **Depends on:** Files 18–19, 23.
- **Unlocks:** nothing further in this stage; you now have the complete, real schema in your head.

### 25. `docs/DATABASE_DESIGN.md`
- **Read now because:** you've just read every table as code and as migration DDL — now read the narrative doc that explains *why* each design choice was made, while it's still fresh.
- **You'll learn:** the documented rationale behind the schema decisions you just traced in code (RBAC shape, tenant scoping, the pgvector chunk design, `agent_executions`' role).
- **Depends on:** Files 14–24.
- **Unlocks:** every `core/`, `retrieval/`, `agents/` service file from here on — you'll recognize this doc being cited by section number in their docstrings.

---

## Stage D — Core: Tenancy (Files 26–28)

The multi-tenancy root. Read this before users/auth/audit/incidents, since all of them are organization-scoped and several (users, in particular) get called *from* tenancy.

### 26. `app/core/tenancy/schemas.py`
- **Read now because:** schemas-before-repository-before-service is the consistent three-file pattern you'll now see in every `core/` submodule — start the pattern here.
- **You'll learn:** `OrganizationStatus`, `SSOProvider`/`SSOProtocol`, `ConnectorSource`/`ConnectorStatus`, `AccessRuleType`, `InvitationStatus`, and the request/response Pydantic models for each.
- **Depends on:** File 10 conceptually (the shared-vocabulary pattern).
- **Unlocks:** File 27.

### 27. `app/core/tenancy/repository.py`
- **Read now because:** pure data access, one statement per function — the fastest way to see exactly which queries tenancy needs before reading the business logic that calls them.
- **You'll learn:** CRUD-shaped functions for organizations, projects, SSO config, connector configs, access rules, invitations.
- **Depends on:** Files 14, 26.
- **Unlocks:** File 28.

### 28. `app/core/tenancy/service.py`
- **Read now because:** this is where the actual business rules live — read it fully now (not partially, the way an earlier draft of this project treated it).
- **You'll learn:** `create_organization` (auto-creates the default "General" project in the same transaction), `configure_sso`, `register_connector`, `evaluate_provisioning` (the precedence chain: pending invitation → domain rule → group rule → deny — this is the heart of SSO auto-provisioning), and the repeated `_ensure_same_organization` tenant-isolation guard pattern you'll see copied (not shared) into audit and incidents later.
- **Depends on:** Files 9, 11, 26–27, and (forward reference, resolved once you read Stage E) `core/users`.
- **Unlocks:** Stage F (`core/auth`, which calls `evaluate_provisioning` during SSO login) and Stage J (`ingestion`, which calls `register_connector`/`update_connector_sync_status`).

---

## Stage E — Core: Users (Files 29–31)

RBAC and the one function that turns "a user id + an org id" into the `Identity` object you already know from Stage B.

### 29. `app/core/users/schemas.py`
- **You'll learn:** `Permission`, `Role`, `UserProfile` (the richer human-facing counterpart to `Identity`).
- **Depends on:** File 9.
- **Unlocks:** File 30.

### 30. `app/core/users/repository.py`
- **You'll learn:** `get_role_names`/`get_permission_codes` (both organization-scoped joins — this is where multi-tenant RBAC is actually enforced at the query level).
- **Depends on:** Files 15, 29.
- **Unlocks:** File 31.

### 31. `app/core/users/service.py`
- **Read now because:** `resolve_identity` is *the* function that constructs every real (non-agent) `Identity` in the system — you met the type in Stage B, now you see how it's populated.
- **You'll learn:** `resolve_identity` (raises `NotFoundError`/`PermissionDeniedError` for missing/inactive users; empty roles/permissions is a valid fail-closed result, not an error), `get_or_create_user` (global by email, not per-org — one person can belong to multiple orgs), `authorize`/`require_permission` (pure, in-memory permission checks against the pre-resolved sets on `Identity`).
- **Depends on:** Files 9, 11, 29–30.
- **Unlocks:** Stage F (`core/auth` calls `resolve_identity`/`get_or_create_user`) and every later `require_permission(...)` call across the whole codebase.

---

## Stage F — Core: Auth (Files 32–34)

SSO/OIDC login and session management. This is the most substantial of the core submodules — take your time on the service file.

### 32. `app/core/auth/schemas.py`
- **You'll learn:** the OIDC Authorization Code + PKCE flow's shapes — `SSOAuthorizationRedirect`, `SSOCallbackRequest`, `SessionTokens`, `TokenClaims` (deliberately smaller than `Identity` — no roles/permissions), `VerifiedIdPClaims`.
- **Depends on:** nothing new.
- **Unlocks:** File 33.

### 33. `app/core/auth/repository.py`
- **You'll learn:** refresh-token CRUD including `revoke_family` (the bulk-revoke response to detected token reuse) and the external-identity-mapping lookups (note: reads/writes `ExternalIdentityMapping`, which is defined in `tenancy_models.py`, not here — the cross-file ownership you were told to watch for back in File 14).
- **Depends on:** Files 14, 16, 32.
- **Unlocks:** File 34.

### 34. `app/core/auth/service.py`
- **Read now because:** this is the richest single file in `core/` — the whole SSO login lifecycle in one place.
- **You'll learn:** `begin_sso_login` (PKCE challenge + cached OIDC discovery), `complete_sso_login` (real ID-token verification against the issuer's JWKS via `python-jose`), `_resolve_or_provision_user` (delegates to `tenancy_service.evaluate_provisioning`, which you read in File 28), `refresh` (rotation + reuse-detection — a presented-but-revoked token triggers whole-family revocation), `logout`/`revoke_all_sessions`, `verify_access_token` (pure JWT decode, no DB). Also note the one genuine placeholder here: `_resolve_client_secret` returns the config's `client_secret_ref` unchanged — real envelope-encryption/secret-resolution doesn't exist yet.
- **Depends on:** Files 9, 11, 28, 31, 33.
- **Unlocks:** Stage S (`mcp/auth.py` calls `verify_access_token` directly).

---

## Stage G — Core: Audit (Files 35–37)

Small and quick — an append-only trail every mutating operation in `core/` writes to.

### 35. `app/core/audit/schemas.py`
- **You'll learn:** `AuditLogEntry`, `AuditLogQuery` (deliberately excludes `organization_id` — a mandatory separate argument, not a filter).
- **Unlocks:** File 36.

### 36. `app/core/audit/repository.py`
- **You'll learn:** `insert` (append-only — no update/delete function exists at all, making "append-only" structural, not just a convention) and `list_entries`.
- **Depends on:** Files 15, 35.
- **Unlocks:** File 37.

### 37. `app/core/audit/service.py`
- **You'll learn:** `record_audit_event` — the function you'll now recognize being called at the end of nearly every mutating operation in `core/tenancy`, `core/incidents`, and elsewhere; `query_audit_log` (organization-scoped).
- **Depends on:** Files 9, 11, 36.
- **Unlocks:** you'll now understand the `await record_audit_event(...)` call you already skimmed past in File 28, and will see again repeatedly.

---

## Stage H — Core: Incidents (Files 38–40)

The incident + postmortem system of record — the domain object the whole Investigation/Postmortem Agent arc (much later) revolves around.

### 38. `app/core/incidents/schemas.py`
- **You'll learn:** `Incident`, `IncidentUpdate`/`IncidentFilter`, `TimelineEntry`/`TimelineNoteCreate`, `ActionItem`, `Postmortem`/`PostmortemUpdate`.
- **Depends on:** File 9 (reuses `Severity`/`IncidentStatus`/`PostmortemStatus`/`ActionItemStatus` from `shared/schemas`, doesn't redefine them).
- **Unlocks:** File 39.

### 39. `app/core/incidents/repository.py`
- **You'll learn:** incident/timeline/postmortem CRUD, including `list_postmortems_by_organization` (org-scoped, not project-scoped — `postmortems` has no `project_id` column) and the generic `**fields`-driven `update_incident`/`update_postmortem`.
- **Depends on:** Files 15, 38.
- **Unlocks:** File 40.

### 40. `app/core/incidents/service.py`
- **Read now because:** this file will matter enormously later (the Investigation and Postmortem Agents both call directly into it), so build a solid model of it now while it's simple.
- **You'll learn:** `create_incident`, `get_incident`/`list_incidents`/`update_incident` (with automatic `resolved_at` stamping), `add_timeline_note` (human-authored only — `event_type="note"`) and, importantly, `record_investigation_result` (the *agent*-authored timeline write path, `event_type="investigation"` — a good example of a public interface function added later specifically because a downstream agent needed it), `create_postmortem`/`get_postmortem`/`list_recent_postmortems`/`update_postmortem`/`approve_postmortem` (the mandatory human-review gate — nothing an agent drafts becomes "knowledge" without this).
- **Depends on:** Files 9, 11, 37, 38–39.
- **Unlocks:** Stage O (Investigation Agent, which calls `list_recent_postmortems` and `record_investigation_result`) and Stage P (Postmortem Agent, which calls `get_timeline`/`create_postmortem`) much later — this file is a direct dependency of both.

---

## Stage I — Retrieval Library (Files 41–46)

The pure hybrid-search library — no knowledge of agents, tenancy, or incidents. Read it before ingestion, since ingestion's final step calls into it.

### 41. `app/retrieval/schemas.py`
- **You'll learn:** `CollectionName` (`"documentation"`/`"code"`/`"conversations"`), `SearchFilters`, `ScoredChunk`, `UpsertChunk`. Note `ScoredChunk.metadata` — a `dict[str, str]` folded from that document's `document_metadata` EAV rows, populated only when a caller opts in (see File 42); added specifically so the Investigation Agent (Stage O) can tell a GitHub file chunk apart from a commit/PR/issue chunk by its `metadata["kind"]`, without the higher-volume Answer Agent path paying for a join it never needs.
- **Unlocks:** File 42.

### 42. `app/retrieval/interfaces/base.py`
- **You'll learn:** the `VectorStore` protocol — the abstraction `PgVectorStore` (File 44) implements, and the one a future Qdrant-backed store would implement identically. Both `search`/`lexical_search` take an `include_metadata: bool = False` keyword-only parameter — opt-in, additive, defaulting off so every pre-existing caller's query cost is unchanged.
- **Depends on:** File 41.
- **Unlocks:** File 44.

### 43. `app/retrieval/embedding.py`
- **You'll learn:** `embed_query`/`embed_texts` — the single batched embedding-model call site (`all-MiniLM-L6-v2`, 384-dim, per ENGINEERING_DECISIONS.md #006).
- **Depends on:** File 7.
- **Unlocks:** Files 44 and 46.

### 44. `app/retrieval/pgvector/store.py`
- **You'll learn:** `PgVectorStore.search` (dense, pgvector inner-product) and `.lexical_search` (Postgres `ts_rank_cd` over the `content_tsv` column from File 18) — both hard `WHERE`-filtered by tenant/project/ACL, never post-filtered. `_load_metadata_by_document` is the `include_metadata=True` implementation: one extra batched query per call (not one per chunk), short-circuiting to an empty dict without a round-trip when no caller asked for metadata.
- **Depends on:** Files 18, 41–42.
- **Unlocks:** File 46.

### 45. `app/retrieval/ranking/fusion.py`
- **You'll learn:** `reciprocal_rank_fusion` — how dense and lexical result lists get combined into one ranked list (`_DEFAULT_K = 60`).
- **Depends on:** File 41.
- **Unlocks:** File 46.

### 46. `app/retrieval/service.py`
- **Read now because:** this is the *only* entry point other modules should call — everything above it is internal wiring.
- **You'll learn:** `search` (hybrid search fanned out across up to 3 collections × 2 modes = up to 6 sequential queries per call — necessarily sequential since they share one `AsyncSession`; the optional `collection` parameter added later for the Investigation Agent's per-source searches, and the optional `include_metadata` parameter, added alongside it, that threads File 42/44's opt-in metadata join through this facade), `upsert` (embeds once, batched, then groups by collection), `delete`.
- **Depends on:** Files 41–45.
- **Unlocks:** Stage J (`ingestion/service.py` calls `upsert`), Stage L (Retrieval Agent calls `search`), Stage O (Investigation Agent calls `search` with a specific `collection`).

---

## Stage J — Ingestion Pipeline (Files 47–57)

Fetch → clean → chunk → embed-and-store, running as an async worker process, not inline with a web request.

### 47. `app/ingestion/schemas.py`
- **You'll learn:** `ResolvedConnectorConfig`, `RawItem`, `ProcessedDocument` (including `content_type: ContentType`), `UpsertChunk`-adjacent shapes.
- **Depends on:** File 41 (`CollectionName`-adjacent concepts).
- **Unlocks:** File 48.

### 48. `app/ingestion/connectors/base.py`
- **You'll learn:** the connector protocol (`authenticate`/`fetch_batch`/`normalize`) every source-specific connector implements.
- **Depends on:** File 47.
- **Unlocks:** Files 49–50.

### 49. `app/ingestion/connectors/slack.py`
- **You'll learn:** a concrete implementation of the protocol from File 48, for Slack.
- **Depends on:** File 48.
- **Unlocks:** nothing further; a second example (File 50) reinforces the pattern.

### 50. `app/ingestion/connectors/github.py`
- **You'll learn:** the same protocol, implemented for GitHub — fetches repository files (a tree walk on full sync, changed-file diffing on incremental sync, unchanged since this connector's first version) *plus* commits, pull requests, and issues, each phase driven by a phase-aware cursor (`{repo_index, phase, page}` cycling `"files" → "commits" → "pulls" → "issues"` per repo). `normalize()` dispatches per an internal `"_kind"` tag rather than assuming every raw item is a file. Notice the two sync-strategy asymmetries: commits/issues support GitHub's server-side `since` filter directly, but `/pulls` has none, so incremental PR sync is a client-side cutoff against a descending-`updated_at`-sorted page instead. Also notice the deliberately *not* de-duplicated cost: an incremental sync's commit-detail fetch happens once for file-diffing and again for commit-evidence, flagged in the module docstring rather than cached across phases (this connector is stateless between calls, so there's nowhere safe to stash that cache).
- **Depends on:** File 48.
- **Unlocks:** Stage O's evidence-gathering docstring will make immediate sense — this is the connector that produces the commit/PR/issue chunks it searches for.

### 51. `app/ingestion/processors/cleaning.py`
- **You'll learn:** raw-content normalization before chunking.
- **Unlocks:** File 54.

### 52. `app/ingestion/processors/metadata.py`
- **You'll learn:** metadata extraction from raw items.
- **Unlocks:** File 54.

### 53. `app/ingestion/processors/chunking.py`
- **You'll learn:** how documents are split into chunks, and `ContentType` (imported from `ingestion/schemas.py`, File 47 — moved there specifically to avoid an import cycle).
- **Depends on:** File 47.
- **Unlocks:** File 54.

### 54. `app/ingestion/processors/pipeline.py`
- **You'll learn:** `process_document` — the function composing cleaning → metadata → chunking into one call, populating `ProcessedDocument.content_type`.
- **Depends on:** Files 51–53.
- **Unlocks:** File 56.

### 55. `app/ingestion/repository.py`
- **You'll learn:** `IngestionJob`/`Document`/`DocumentMetadata` CRUD.
- **Depends on:** File 17.
- **Unlocks:** File 56.

### 56. `app/ingestion/service.py`
- **Read now because:** this is where everything in this stage gets composed, and where it hands off to the retrieval library you already understand.
- **You'll learn:** `run_ingestion_job` (the job lifecycle: running → succeeded/failed, the same shape you'll recognize in `agents/service.py` much later), `_process_one_item` (builds `UpsertChunk`s via a content-type-to-collection mapping and calls `retrieval_service.upsert` — File 46 — inside a savepoint), and the `Identity.for_agent("ingestion_worker", ...)` pattern (File 9) for its own internal, non-human writes.
- **Depends on:** Files 9, 28 (`register_connector`/`update_connector_sync_status`), 46, 54–55.
- **Unlocks:** File 57.

### 57. `app/ingestion/workers/tasks.py` and `app/ingestion/workers/main.py`
- **You'll learn:** the arq task definitions and `WorkerSettings` — this is one of only two real process entrypoints in the whole codebase (the other is Stage T's `run_mcp_server.py`). Run via `arq app.ingestion.workers.main.WorkerSettings`. `main.py`'s `WorkerSettings` also declares `cron_jobs = [cron(scheduled_reconciliation, minute=0)]` — an *hourly* job (fires at the top of every hour) that is, today, the only thing in this codebase that triggers ingestion automatically: `scheduled_reconciliation` (in `tasks.py`) lists every active `connector_config` and enqueues `run_ingestion_job_task` for each one, which then runs an incremental sync. Nothing else in `app/` enqueues that task. `docs/PROJECT_PLAN.md` section 4.4 also describes a webhook-driven path (Slack Events API, GitHub webhooks, ...) for near-real-time ingestion alongside this scheduled-polling path — that half is documented design only; no webhook receiver exists anywhere in `app/` yet.
- **Depends on:** File 56.
- **Unlocks:** a complete mental model of "how does a Slack/GitHub message actually end up as a searchable chunk, automatically" — you now have the full ingestion data flow end-to-end, including what actually triggers it. Also unlocks Files 77-77d's live-evidence extension: that feature exists specifically to cover the gap between this hourly cadence and "right now" for an active incident investigation.

---

## Stage K — Agents Foundation (Files 58–65)

The shared scaffolding every LangGraph node in this project is built on, plus the two spec docs that describe what each node (most not yet read) is supposed to do.

### 58. `app/shared/schemas/agent_contracts.py`
- **You'll learn:** `Citation`, `EvidenceItem`, `RootCauseHypothesis`, `InvestigationResult`, `AskResponse` — the cross-module output types `agents/` produces, deliberately placed in `shared/` (not `agents/schemas.py`) because `core/incidents` also needs to consume `InvestigationResult` (you'll see this exact import in Stage H's `record_investigation_result`, now retroactively justified). `EvidenceItem.source` includes `"issue"` alongside `"pull_request"`/`"commit"` (all three built ahead of the GitHub connector actually producing them); `source_timestamp` (the original GitHub object's own date, not when evidence-gathering ran) and `metadata` (kind-specific facts — author, labels, changed files, reviews — carried through as structured fields rather than folded into `summary`'s prose) are additive, default-empty fields so every pre-existing construction site keeps working unchanged.
- **Depends on:** nothing new.
- **Unlocks:** everything from here through Stage Q.

### 59. `app/agents/schemas.py`
- **You'll learn:** `AgentExecution` (the read-side Pydantic view of the ORM row from File 19).
- **Depends on:** Files 10, 19.
- **Unlocks:** File 60.

### 60. `app/agents/repository.py`
- **You'll learn:** `insert_agent_execution`/`update_agent_execution` — the running→succeeded/failed bookkeeping every agent entry point uses.
- **Depends on:** Files 19, 59.
- **Unlocks:** Stage Q (`agents/service.py`).

### 61. `app/agents/llm.py`
- **You'll learn:** `get_llm()` — the `@lru_cache`-wrapped `ChatOpenAI` factory (model from `Settings.agent_llm_model`).
- **Depends on:** File 7.
- **Unlocks:** every node that needs an LLM call, from Stage L onward.

### 62. `app/agents/retry.py`
- **You'll learn:** `call_with_retry` — the shared up-to-2-retries-with-backoff helper, generic over any `retry_count: dict[str, int]` (not tied to `GraphState` specifically — you'll see it reused outside the graph in Stage P).
- **Unlocks:** Stages L, N, O, P — every node's retry logic.

### 63. `app/agents/graph.py` — **first pass, `GraphState` only**
- **Read now because:** you need the shared state shape before reading any individual node, but you don't yet have the node modules to understand `build_graph`'s wiring — so stop after `GraphState` and `_route_after_confidence` on this pass; you'll come back for the rest in Stage Q.
- **You'll learn:** `GraphState`'s full field set (`query`/`incident_id`/`actor`, `retrieved_chunks`/`rewritten_query`, `confidence_score`/`confidence_signals`, `route`, `evidence`/`hypotheses`, `result`, `retry_count`/`terminal_error`) — this is the one object every node reads from and returns partial updates to.
- **Depends on:** Files 9, 41, 58.
- **Unlocks:** every node file from Stage L through P (they all type-hint against `GraphState`); you'll return to the rest of this file in Stage Q.

### 64. `docs/AGENT_WORKFLOWS.md`
- **Read now because:** you now know the shared state shape (`GraphState`) that every section of this doc describes populating — read it in full now, before any individual node.
- **You'll learn:** the full agent-by-agent spec: Retrieval Agent, Confidence Evaluation, Answer Agent, Investigation Agent (evidence sub-stage A / hypothesis sub-stage B), Postmortem Agent, Knowledge Gap Agent (not yet built), plus section 4's two-tier failure-handling rule and section 11.3's request-flow diagram.
- **Depends on:** Files 58, 63.
- **Unlocks:** Stages L–Q directly — you'll recognize almost every docstring from here on as implementing one paragraph of this file.

### 65. `docs/API_DESIGN.md`
- **Read now because:** it's the REST/MCP contract layer spec — read alongside File 64 since they cover the same ground from the "external interface" angle instead of the "internal workflow" angle.
- **You'll learn:** the `agents/` public interface signatures (`answer_question`/`triage_incident`/`generate_postmortem`/`detect_knowledge_gaps`), the intended MCP tool table (`ask_question`, `investigate_incident`, etc. — none implemented yet, you'll confirm this yourself in Stage S), and the "verified evidence vs. AI-generated hypothesis" distinction that shapes `EvidenceItem`/`RootCauseHypothesis` (File 58).
- **Depends on:** File 64.
- **Unlocks:** Stage S (`mcp/`) — you'll immediately recognize which of this doc's tool table is and isn't implemented.

---

## Stage L — Retrieval Agent Node (Files 67–70)

The first real LangGraph node. Distinct from the `retrieval/` library (Stage I) — this is the agent that *calls* that library.

### 67. `app/agents/retrieval/rewriting.py`
- **You'll learn:** query rewriting — the optional LLM call that reformulates a user's question before searching.
- **Depends on:** Files 61–62.
- **Unlocks:** File 70.

### 68. `app/agents/retrieval/reranking.py`
- **You'll learn:** cross-encoder reranking (`cross-encoder/ms-marco-MiniLM-L-6-v2`, ENGINEERING_DECISIONS.md #009) — and the important, self-caught detail that reranking overwrites `ScoredChunk.score` in place, which is why the node (File 70) captures the pre-rerank fused score separately before calling this.
- **Depends on:** File 41.
- **Unlocks:** File 70.

### 69. `app/agents/retrieval/context_assembly.py`
- **You'll learn:** how reranked chunks get assembled into the context block the Answer Agent (Stage N) will eventually consume.
- **Depends on:** File 41.
- **Unlocks:** File 70.

### 70. `app/agents/retrieval/node.py`
- **You'll learn:** `make_retrieval_agent_node(session, llm)` — the factory pattern (used because LangGraph's calling convention is fixed to `(state)`, but this node needs a request-scoped `session`/`llm` closed over) that composes rewriting → `retrieval.service.search` (File 46) → reranking → context assembly, and seeds `confidence_signals["top_similarity"]` from the pre-rerank score.
- **Depends on:** Files 46, 63, 67–69.
- **Unlocks:** Stage M (Confidence Evaluation reads `state.retrieved_chunks`/`confidence_signals` this node sets).

---

## Stage M — Confidence Evaluation (File 71)

### 71. `app/agents/confidence.py`
- **You'll learn:** `evaluate_confidence`/`confidence_evaluation_node` — how retrieval signals get normalized and combined into `state.confidence_score`, and `_route_after_confidence`'s threshold logic (against `Settings.confidence_threshold`, File 7) that decides Answer Agent vs. Investigation Agent.
- **Depends on:** Files 7, 63, 70.
- **Unlocks:** Stages N and O both — this node's output is what routes between them.

---

## Stage N — Answer Agent (Files 72–76)

Reached only when confidence is high enough. Generation with inline citation markers, then a grounding-verification pass before anything is returned.

### 72. `app/agents/answer/markers.py`
- **You'll learn:** the `[n]` inline-citation-marker format and `strip_markers` (removes them for the final display text).
- **Unlocks:** File 73.

### 73. `app/agents/answer/generation.py`
- **You'll learn:** `generate_answer`/`build_context_block`/`is_no_answer` — the generation prompt, constrained to retrieved chunks only, with a literal `NO_ANSWER` sentinel for "the context doesn't answer this."
- **Depends on:** Files 61, 72.
- **Unlocks:** File 76.

### 74. `app/agents/answer/grounding.py`
- **You'll learn:** `split_sentences`/`verify_grounding` — sentence-level embedding-similarity grounding checks with a three-band threshold (grounded/ungrounded/ambiguous, the ambiguous band escalating to one LLM yes/no call).
- **Depends on:** Files 43, 61.
- **Unlocks:** File 76.

### 75. `app/agents/answer/citations.py`
- **You'll learn:** `build_citations` — extracting real `Citation` objects (File 58) from marker-bearing text, only for sentences that survived grounding.
- **Depends on:** Files 58, 72.
- **Unlocks:** File 76.

### 76. `app/agents/answer/node.py`
- **You'll learn:** `make_answer_agent_node(llm)` — composes generate → split → verify → extract-citations → strip-markers, with `_UngroundedAnswerError` triggering a fresh generation attempt (via `call_with_retry`, File 62) rather than trying to repair an ungrounded draft, and a final honest "insufficient grounded information" fallback if retries are exhausted.
- **Depends on:** Files 62–63, 73–75.
- **Unlocks:** nothing further in this stage; you now understand one full leaf of the graph (`route == "answer"`).

---

## Stage O — Investigation Agent (Files 77–79, plus 77a–77d)

Reached when confidence is too low. Two structurally separate sub-stages: verified evidence gathering, then AI-generated hypothesis generation — kept in separate files specifically so that distinction is structural, not a prompt convention.

### 77. `app/agents/investigation/evidence.py`
- **You'll learn:** `gather_evidence` — six sources in priority order (GitHub evidence, Slack via `collection="conversations"`, Jira — a real, flagged gap, no connector exists — existing postmortems via `core.incidents.list_recent_postmortems`, monitoring/alerts — mocked, returns empty, and now a sixth, optional *live evidence* source), short-circuiting at `_EVIDENCE_CAP`, each source's failure logged and skipped rather than fatal. The GitHub source (`_gather_code_evidence`) is not a single `collection="code"` search: it searches "code" (file chunks) *and* "documentation" (commit/PR/issue chunks — that's where their prose lands, since `classify_content_type`, File 53, has no code-extension to key off of for them), both with `include_metadata=True`, merges and re-ranks the two result lists, and filters the "documentation" side down to chunks whose metadata actually carries a `repo` key. `_chunk_to_evidence` derives each `EvidenceItem.source` from `chunk.metadata["kind"]` (`"commit"`/`"pull_request"`/`"issue"`; a plain file chunk has no `"kind"` key and falls back to `"github"`) rather than the caller stamping one fixed value on every result.

  The sixth source is this module's hybrid extension: `gather_evidence` now takes an optional `incident_id`, and after the five indexed sources above, `_should_augment_with_live_evidence` decides whether to also call `_gather_live_evidence` -- true if `incident_id is not None` (a genuinely active incident, not just a low-confidence question), or fewer than 3 indexed items were found, or the freshest timestamped indexed item is older than 2 hours (or nothing is timestamped at all). Deliberately *not* gated on `GraphState.confidence_score`: by the time this function runs, confidence was either already established as too low (the confidence-routed `answer_question` path) or never computed at all (`triage_incident`'s dedicated graph skips Retrieval/Confidence Evaluation entirely, per File 63's docstring) -- `incident_id` is the one signal actually available here. `_gather_live_evidence` resolves the org's registered `connector_configs` via `core.tenancy.service.list_connectors` (never `app.ingestion`, which `agents/` may not import at all -- see Files 77a-77d) and dispatches each active one to the matching live source.
- **Depends on:** Files 40, 46, 50, 58, 62–63, 77a–77d.
- **Unlocks:** Files 77a–77d directly (this is their only caller), and File 78.

### 77a. `app/agents/investigation/live/base.py`
- **Read now because:** this tiny file is the whole live-evidence extension's interface contract -- read it before either concrete implementation, the same "protocol before implementations" order Files 41-42/48 already established for `retrieval`/`ingestion`.
- **You'll learn:** `LiveEvidenceSource`, a `Protocol` with one method, `fetch_live_evidence(connector_config, query, since, limit) -> list[EvidenceItem]` -- deliberately *not* a reuse of `ingestion.connectors.base.Connector` (File 48): a live lookup is a single-shot, narrow, incident-scoped query with no pagination cursor and no persistence, fundamentally unlike a full resumable sync destined for the chunk/embed pipeline. Keeping this protocol entirely inside `agents/investigation/` is what lets this whole feature exist without `agents/` ever importing `app.ingestion` (forbidden by this project's import-linter contract, File 1).
- **Depends on:** File 58 (`EvidenceItem`), File 26 (`core.tenancy.schemas.ConnectorConfig`, not ingestion's own `ResolvedConnectorConfig`).
- **Unlocks:** Files 77b–77d.

### 77b. `app/agents/investigation/live/github_live.py`
- **You'll learn:** `GitHubLiveSource` -- fetches commits/PRs/issues live via GitHub's *Search* API (`/search/issues`, `/search/commits`), not the plain list endpoints File 50's ingestion connector uses, since only Search supports a free-text relevance query (`q=<incident text> repo:owner/name`). A real, flagged trade-off: GitHub's Search API allows only 30 requests/minute (vs. 5,000/hour for the general REST API), so this is capped to 5 repos per call, and PR results skip fields only available from a per-PR detail call (`merged_at`, changed files, reviews) that File 50's connector does fetch -- a live result is a fast, partial glance, not a full-fidelity indexed record.
- **Depends on:** File 77a.
- **Unlocks:** File 77c reinforces the same pattern.

### 77c. `app/agents/investigation/live/slack_live.py`
- **You'll learn:** `SlackLiveSource` -- reuses `conversations.history` (the same bot-token-scoped endpoint File 49's ingestion connector already uses) across the configured channels, filtered by a naive client-side keyword match. Deliberately does *not* use Slack's `search.messages` API even though that would give real relevance-ranked search: that endpoint requires a *user* token (`xoxp-...`), and this system's `ConnectorConfig.credential_ref` for Slack is the same bot token (`xoxb-...`) File 49's connector already treats as a placeholder -- there is no user-scoped credential modeled anywhere in this system. A real, documented fidelity gap, not silently worked around.
- **Depends on:** File 77a.
- **Unlocks:** File 77d.

### 77d. `app/agents/investigation/live/monitoring_live.py`
- **You'll learn:** `MonitoringLiveSource` -- a no-op stub (always returns no evidence, exactly like `evidence._gather_monitoring_evidence`) implementing the same `LiveEvidenceSource` protocol, proving it generalizes beyond GitHub/Slack. Not actually wired into `evidence._LIVE_SOURCES` -- there's no `ConnectorSource` value for "monitoring" (File 26) and no `connector_configs` row a monitoring source could resolve from yet. Exists so a future real integration is a drop-in replacement, per this feature's "future-ready interface" requirement.
- **Depends on:** File 77a.
- **Unlocks:** nothing further in this stage; returns you to File 78.

### 78. `app/agents/investigation/hypothesis.py`
- **You'll learn:** `generate_hypotheses` — one LLM call over the assembled evidence, prompted for a JSON object, validated so that any hypothesis citing no real (or fabricated) `EvidenceItem.reference` is silently rejected, never surfaced. `_build_evidence_block`/`_format_evidence_line` render each item's `source_timestamp` (when known) and `metadata` facts (author, changed files, labels, ...) alongside `reference`/`source`/`summary`, so the model can reason over who authored a commit/PR/issue and when, not just its prose summary. Unchanged by the live-evidence extension: since live evidence items also populate `metadata` (stamped with `"retrieval_mode": "live"`), this function surfaces that distinction to the LLM automatically, with no code change needed here.
- **Depends on:** Files 58, 61, 77.
- **Unlocks:** File 79.

### 79. `app/agents/investigation/node.py`
- **You'll learn:** `make_investigation_agent_node(session, llm)` — orchestrates evidence → hypotheses, short-circuits to a "no automated evidence found" result when evidence is empty (no wasted LLM call), and — a detail worth noticing — best-effort writes its result back to the incident's timeline via `core.incidents.service.record_investigation_result` (File 40) whenever `state.incident_id` is set, so a *later* Postmortem Agent run can find it. This node is also what threads `state.incident_id` into `gather_evidence`'s optional `incident_id` parameter (File 77) -- the signal `_should_augment_with_live_evidence` checks first.
- **Depends on:** Files 40, 62–63, 77–78, 77a–77d.
- **Unlocks:** Stage P (Postmortem Agent's root-cause step reads exactly what this node writes) and Stage Q (`build_investigation_graph`).

---

## Stage P — Postmortem Agent (Files 80–83)

Not reached via the confidence-routed graph at all — triggered separately, after a human marks an incident resolved. A linear pipeline, no routing logic.

### 80. `app/agents/postmortem/timeline.py`
- **You'll learn:** `build_narrative` (renders a mixed note+investigation timeline into one chronological text block) and `latest_investigation_hypotheses` (pulls the most recent investigation entry's hypotheses back out — the exact data File 79 wrote).
- **Depends on:** Files 38, 79 (conceptually — reads what it wrote).
- **Unlocks:** File 82.

### 81. `app/agents/postmortem/root_cause.py`
- **You'll learn:** `extract_root_cause` — one LLM call that either adopts the candidate hypothesis (if the timeline doesn't later contradict it) or derives a fresh root cause, both branches handled in a single prompt rather than a separate contradiction-detection step.
- **Depends on:** Files 61, 80.
- **Unlocks:** File 83.

### 82. `app/agents/postmortem/action_items.py`
- **You'll learn:** `generate_action_items` — a second LLM call producing candidate `ActionItem`s (File 38), always forced to `status="open"` regardless of what the model proposes.
- **Depends on:** Files 38, 61.
- **Unlocks:** File 83.

### 83. `app/agents/postmortem/pipeline.py`
- **You'll learn:** `run_postmortem_pipeline` — the plain async composition of Files 80–82 (deliberately not a LangGraph node: this pipeline has no branching for LangGraph's machinery to add value over).
- **Depends on:** Files 80–82.
- **Unlocks:** Stage Q (`agents/service.py`'s `generate_postmortem` calls this directly).

---

## Stage Q — Graph Assembly & the Agents Public Interface (Files 84–85)

Now that every node exists, come back and read the two "composing layer" files in full — their forward references from Stage K now resolve completely.

### 84. `app/agents/graph.py` — **second pass, in full**
- **Read now because:** every node import inside `build_graph`/`build_investigation_graph` now refers to a file you've actually read.
- **You'll learn:** `build_graph(session, llm)` (Retrieval Agent → Confidence Evaluation → conditional edge → Answer Agent or Investigation Agent) and `build_investigation_graph(session, llm)` (a second, separate compiled graph containing *only* the Investigation Agent node, built for `triage_incident`, which always investigates and bypasses Retrieval/Confidence entirely) — plus why both node imports are deferred to call-time inside these functions (breaking a circular import with `GraphState`).
- **Depends on:** Files 63, 70–71, 76, 79.
- **Unlocks:** File 85.

### 85. `app/agents/service.py`
- **Read now because:** this is `agents/`'s entire public interface, and every function in it now makes complete sense.
- **You'll learn:** `answer_question` (builds `build_graph`'s initial state, delegates to the shared `_run_graph_and_record` helper), `triage_incident` (resolves the incident via `core.incidents.get_incident`, builds a query from title+description, enters directly at `build_investigation_graph`), `generate_postmortem` (calls `run_postmortem_pipeline`, File 83, then persists via `core.incidents.create_postmortem` under an internal `Identity.for_agent("postmortem_agent", ...)` actor so `generated_by` comes out exactly right), and `_run_graph_and_record` (the shared record-execution/invoke-graph/handle-failure bookkeeping, including the two-tier `EKIPError`-vs-unexpected-exception handling from File 11 — and note `generate_postmortem` deliberately does *not* use this shared path, since a fabricated "degraded" `Postmortem` would be actively misleading to a human reviewer in a way a fabricated `AskResponse` apology is not).
- **Depends on:** Files 11, 40, 60–61, 83–84.
- **Unlocks:** a complete mental model of "how does a question or an incident actually get answered/investigated/written up" — this closes the loop the whole `agents/` stage has been building toward.

---

## Stage R — Core: Observability (Files 86–88)

A small, narrow bridge module — read it now because its *only* purpose is to unblock the MCP stage you're about to read, and it will feel arbitrary read any earlier.

### 86. `app/core/observability/schemas.py`
- **You'll learn:** `McpRequestLog` — read-side view of one `mcp_requests` row (File 20).
- **Unlocks:** File 87.

### 87. `app/core/observability/repository.py`
- **You'll learn:** `insert_mcp_request` — a single insert per completed call (no running→completed lifecycle, unlike `agent_executions`, since every field is known at completion time).
- **Depends on:** Files 20, 86.
- **Unlocks:** File 88.

### 88. `app/core/observability/service.py`
- **Read now because:** this file exists *solely* so `app.mcp` never has to import `app.database` directly — the import-linter contract from File 1 forbids exactly that.
- **You'll learn:** `record_mcp_request` — no permission gate, internal bookkeeping triggered on every MCP call regardless of outcome.
- **Depends on:** Files 86–87.
- **Unlocks:** Stage S directly — `mcp/dispatch.py`'s sole call into `core/`.

---

## Stage S — MCP Layer (Files 89–92)

Real, carefully-reasoned plumbing — but read this stage expecting a skeleton, not a finished feature, **as it stood at the point this stage was originally written: zero tools or resources registered.** That has since changed (Milestone 8, Stage V below) — read this stage for the plumbing design, then Stage V for what actually got plugged into it.

### 89. `app/mcp/auth.py`
- **You'll learn:** `resolve_mcp_identity(session, raw_token)` — composes `core.auth.verify_access_token` (File 34, pure JWT decode) with `core.users.resolve_identity` (File 31, DB-backed role/permission load), resolved fresh per call since streamable-HTTP is stateless.
- **Depends on:** Files 31, 34.
- **Unlocks:** File 90.

### 90. `app/mcp/dispatch.py`
- **You'll learn:** `run_mcp_tool(*, tool_name, raw_token, request_summary, handler)` — the shared wrapper every future tool handler is meant to call through: one session for identity resolution + handler execution, a *separate* session (in a `finally` block) for logging to `mcp_requests` via File 88, so a failed transaction's rollback doesn't also erase its own failure log entry; maps `EKIPError.status_hint` (File 11) to a response status.
- **Depends on:** Files 11, 88–89.
- **Unlocks:** File 91.

### 91. `app/mcp/servers/server.py`
- **You'll learn:** `mcp_server = FastMCP(name="ekip")` targeting streamable-HTTP transport, and `session_factory` — a dependency-inversion seam (`Callable[[], AbstractAsyncContextManager[AsyncSession]] | None`) deliberately left unset here and injected from *outside* `app.mcp` (Stage T) specifically because `app.mcp` itself can never import `app.database.session`.
- **Depends on:** File 11.
- **Unlocks:** File 92.
- **Note:** flagged in the code itself as unverified against the actually-installed `mcp` package (the sandbox this was built in couldn't `pip show mcp`) — treat the transport choice and header-extraction approach as "needs confirmation," not settled fact.

### 92. `app/mcp/servers/main.py`
- **You'll learn (as this file stood at the end of Milestone 6):** this was the concrete, first-hand confirmation of the "no tools registered yet" state — eight tool/resource imports existed here, all commented out. Running the server at that point would expose nothing.
- **Depends on:** File 91.
- **Superseded by:** Stage V, File 100 below — every one of those imports is now live (six tools, two resources, two prompts). Treat this entry as a historical snapshot of the file's *shape*, not its current contents.

---

## Stage T — Entrypoints & Operational Scripts (Files 93–95)

How any of this actually gets run, outside of (nonexistent) HTTP requests.

### 93. `scripts/run_mcp_server.py`
- **You'll learn:** the process entrypoint for the MCP server — lives *outside* `app/mcp` specifically so it's free to import `app.database.session.session_scope` and inject it into `server.session_factory` (File 91) at startup.
- **Depends on:** Files 12, 91–92.
- **Unlocks:** nothing further; closes the MCP arc.

### 94. `scripts/test_connectors.py`
- **You'll learn:** a manual, non-pytest smoke test exercising the Slack/GitHub connectors (Files 49–50) directly, with no database involved.
- **Depends on:** Files 49–50.
- **Unlocks:** nothing further; a good "does my `.env` actually work" sanity check if you want to run it yourself.

### 95. `scripts/test_milestone6.py`
- **Read now because:** it's the single best "how does this all actually connect" file to finish on — it seeds a test org/user/permissions directly via ORM, registers connectors, runs real ingestion, then calls `agents.service.answer_question` and prints the result.
- **You'll learn:** a complete, concrete, runnable trace through every stage of this roadmap in one script — tenancy → users → ingestion → retrieval → agents, end to end.
- **Depends on:** essentially everything in Stages D–Q.
- **Unlocks:** your own ability to run the system end-to-end (`python scripts/test_milestone6.py "<question>"`) and watch this entire roadmap execute in real time.

---

## Stage V — Milestone 8: The API Layer & Real MCP Wiring (Files 98–104)

Everything in Stage S was plumbing waiting for cargo. This stage is the cargo: a REST API surface, the six MCP tools/two resources/two prompts Stage S's imports were commented out for, and the `core/knowledge` module both surfaces depend on for the human-in-the-loop "propose → review → publish/reject" runbook lifecycle (AGENT_WORKFLOWS.md's "verified vs. AI-generated" distinction, extended from evidence into documents).

### 98. `app/core/knowledge/schemas.py`, `repository.py`, `service.py`
- **Read now because:** this is the missing "documents" read/write surface both `propose_runbook_update` (File 100) and the Knowledge Gap Agent (Stage W) need, and neither could be built without it — this is why Milestone 8 had to land before Milestone 9's Knowledge Gap Agent could resolve a gap report's `related_document_id`.
- **You'll learn:** `Document`'s lifecycle states (`proposed`/`published`/`rejected`), `propose_document`/`get_document`/`list_proposed_documents`/`publish_document`/`reject_document` — a small, explicit state machine rather than a generic CRUD surface, gated by a `knowledge:review` permission for the publish/reject/list-proposed actions (propose itself has no gate — anything, including an agent, may propose; only a human reviewer may promote a proposal to published knowledge).
- **Depends on:** Files 9, 11 (`Identity`, `EKIPError`).
- **Unlocks:** Files 100–101 below, and Stage W's `related_document_id` resolution.

### 99. `pyproject.toml`'s new `app.api` import-linter contract
- **Read now because:** it's a two-line diff worth noticing rather than a file worth opening on its own — a new contract was added alongside `api/` to keep it a thin pass-through (`api` may depend on `core`, `agents`, `shared`; nothing may depend on `api`), the same enforced-boundary discipline File 1 taught you, now extended to the newest module.
- **Unlocks:** confidence that Files 101–104 below are genuinely thin — the linter would fail CI otherwise.

### 100. `app/mcp/tools/*.py` and `app/mcp/resources/*.py`
- **You'll learn:** six tool handlers (`ask_question`, `investigate_incident`, `generate_postmortem`, `search_similar_incidents`, `search_recent_changes`, `propose_runbook_update`) and two resources (`incident_resource.py`'s `incident://` , `document_resource.py`'s `document://`) — every one a thin `run_mcp_tool`-wrapped (File 90) call into `agents.service`/`core.knowledge.service`, confirming the dispatch design from Stage S was sound once real handlers were written against it.
- **Depends on:** Files 85 (`agents.service`), 98 (`core.knowledge.service`), 90 (`dispatch.run_mcp_tool`).
- **Unlocks:** File 100 (updated `servers/main.py`, below).

### 101. `app/mcp/prompts/triage_incident_prompt.py`, `draft_postmortem_prompt.py`
- **You'll learn:** MCP's third primitive besides tools/resources — a *prompt* is a reusable, parameterized message template an MCP client can request by name rather than a server-side action; these two wrap the same underlying `triage_incident`/`generate_postmortem` agent calls as guided, human-legible prompt text instead of raw tool invocations.
- **Depends on:** File 85.
- **Unlocks:** nothing further; the last of API_DESIGN.md section 3's three MCP primitive types to land.

### 102. `app/mcp/servers/main.py` (current state)
- **Supersedes File 92's original entry above:** all eight originally-commented-out imports (six tools, two resources) are now live, plus the two prompts from File 101 registered alongside them. This is the file that turns Stage S's skeleton into a server that actually does something when queried.
- **Depends on:** Files 91, 100–101.

### 103. `app/api/main.py`, `deps.py`, `errors.py`, `routers/*.py`
- **Read now because:** this is the FastAPI application object that didn't exist in the original snapshot — `main.py` assembles routers (`auth`, `incidents`, `ask`, `postmortems`, `knowledge`), `deps.py` provides `CurrentIdentity`/`DbSession` dependency-injection types every router function takes, `errors.py` maps `EKIPError.status_hint` (File 11) to HTTP responses (the REST-side sibling of `mcp/dispatch.py`'s same mapping, File 90).
- **You'll learn:** every router is a thin pass-through (per the File 99 contract) — `routers/knowledge.py`'s `/knowledge/proposed`, `/publish`, `/reject` call straight into `core.knowledge.service` (File 98); `/knowledge/gaps` (added in Stage W, File 116) is the one exception wired in a later milestone.
- **Depends on:** Files 9, 11, 31, 34, 85, 98.
- **Unlocks:** an actual runnable HTTP server — `uvicorn app.api.main:app` now does something, closing the "no FastAPI application object anywhere" gap the original snapshot noted.

### 104. `tests/core/knowledge/`, `tests/api/`
- **Read now because:** these are the first tests exercising the REST/knowledge surfaces at all — `TestClient` + `dependency_overrides` + a stubbed service layer (the same style you'll see reused in Stage W's REST test, File 116).
- **Depends on:** Files 98, 103.
- **Unlocks:** nothing further; closes Stage V.

---

## Stage W — Milestone 9: The Knowledge Gap Agent (Files 105–116)

A scheduled, cross-tenant background agent — deliberately *not* part of the per-question flow (AGENT_WORKFLOWS.md section 2.6). It never auto-creates a document; it only ever recommends one. Read this stage as a complete worked example of "how does a scheduled AI job fit into a module-boundary-enforced monolith," a question the earlier stages never had to answer.

### 105. `app/shared/config/settings.py` additions, `app/database/models/agent_models.py`'s `KnowledgeGapReport`, its migration
- **You'll learn:** `knowledge_gap_lookback_days`/`knowledge_gap_min_cluster_size`/`knowledge_gap_similarity_threshold` (the tunable knobs); `KnowledgeGapReport`'s columns, in particular `topic_embedding`/`supporting_execution_ids` as JSONB and `related_document_id` as a nullable FK to `documents.id` (File 98) with `ondelete="SET NULL"`.
- **Depends on:** Files 7, 19, 98.
- **Unlocks:** File 108.

### 106. `app/shared/schemas/agent_contracts.py`'s `GapReport`
- **You'll learn:** the read-side shape returned by `detect_knowledge_gaps()`/`list_gap_reports()` (File 110) — mirrors `KnowledgeGapReport`'s columns, with `supporting_execution_ids` as `list[uuid.UUID]` rather than the stored `list[str]`.
- **Depends on:** File 105.

### 107. `app/agents/knowledge_gap/clustering.py`
- **Read now because:** it's pure, dependency-free logic — worth understanding in isolation before the pipeline that calls it. Resolves an open question `AGENT_WORKFLOWS.md` had explicitly flagged as undecided: k-means vs. similarity-threshold clustering. Landed on greedy leader clustering (single-pass, deterministic, no need to know `k` upfront — the number of gap clusters is exactly the unknown quantity this agent exists to discover).
- **You'll learn:** `cosine_similarity`, `cluster_by_similarity` — each new embedding joins the most-similar existing cluster if above threshold, else seeds a new one; ties are broken deterministically (strict `>`, favoring the earlier-created cluster).
- **Unlocks:** File 108.

### 108. `app/agents/knowledge_gap/repository.py`
- **You'll learn:** `list_low_confidence_executions`, `list_open_gap_reports`, `insert_gap_report`, `update_gap_report_supporting_ids` — the module's only DB-touching file, per the "service calls its own repository" convention every earlier module (Files 27-28, etc.) already established.
- **Depends on:** File 105.

### 109. `app/agents/knowledge_gap/pipeline.py`
- **Read now because:** this is where clustering (File 107) and persistence (File 108) meet the two genuinely hard production-AI problems this agent has to solve: *what counts as a gap* (low-confidence `answer_question` executions, filtered to those with a free-text `query` in `input_summary` — the only agent whose executions carry one) and *idempotency on a recurring schedule* (before inserting a new report, check its cluster centroid against every currently-open report's stored embedding via cosine similarity at a *higher* threshold, 0.9, than the clustering pass itself, 0.82 — merging two different topics is a worse failure than an occasional near-duplicate report).
- **You'll learn:** `detect_knowledge_gaps(session, llm, organization_id, ...)` — fetch → filter → embed → cluster → for each cluster ≥ `min_cluster_size`, merge into an existing open report or synthesize a topic via the LLM and resolve a suggested action (`new_runbook` vs. `update_existing`, decided by whether `retrieval.search`'s `documentation` collection returns a match ≥ 0.6).
- **Depends on:** Files 43 (`embedding`), 46 (`retrieval.service`), 107–108.
- **Unlocks:** File 111.

### 110. `app/core/tenancy/repository.py` + `service.py`'s `list_organizations`
- **Read now because:** it's a small, deliberately-flagged exception to "every function takes an `Identity`" — a scheduled job must enumerate every tenant *by definition*, before any per-tenant `Identity` exists to enumerate through. Mirrors the existing precedent `get_organization_sso_config` set (File 28) for a no-actor function.
- **Depends on:** File 26.
- **Unlocks:** File 112.

### 111. `app/agents/service.py`'s `detect_knowledge_gaps`/`list_gap_reports`
- **You'll learn:** the same `agent_executions` bookkeeping pattern every other agent entrypoint in this file already uses (record running → call the pipeline → record succeeded/failed), now taking `actor: Identity` like everything else in this file (not a bare `organization_id`, despite an early draft doing exactly that before self-correcting to match convention) so `actor.organization_id` scopes the pipeline call. `list_gap_reports` is gated by a `knowledge:review` permission (the REST-exposed read should be reviewer-only); `detect_knowledge_gaps` deliberately is *not* gated, since it runs under a permissionless `Identity.for_agent(...)` from a worker, and gating it would break the very thing that's supposed to call it.
- **Depends on:** Files 9, 76 (or wherever `require_permission` lives), 98, 109.
- **Unlocks:** Files 112, 116.

### 112. `app/agents/workers/__init__.py`, `tasks.py`, `main.py`
- **Read now because:** this is the answer to "where does a cross-tenant scheduled agent job actually run inside a modular monolith whose import-linter contracts forbid `ingestion` from importing `agents`?" — a wholly new worker package, separate from `app.ingestion.workers` (File 57), with its own `WorkerSettings`/arq process (`arq app.agents.workers.main.WorkerSettings`).
- **You'll learn:** `scheduled_knowledge_gap_scan` (a 2am cron job, `cron(scheduled_knowledge_gap_scan, hour=2, minute=0)`) calls `tenancy_service.list_organizations` (File 110) then enqueues one `run_knowledge_gap_detection_task` job per organization — so one slow/large tenant can't block the rest — each of which opens its own session via `session_scope` and calls `agents_service.detect_knowledge_gaps` under `Identity.for_agent("knowledge_gap_agent", organization_id)`; retries use the same bounded-exponential-backoff `Retry(defer=...)` pattern as `ingestion.workers.tasks` (File 57), capped at 300 seconds.
- **Depends on:** Files 12, 57 (for the pattern), 110–111.
- **Unlocks:** a third real runnable entrypoint alongside Files 93 and 57.

### 113. `app/api/routers/knowledge.py`'s `GET /knowledge/gaps`
- **You'll learn:** this closes the last unwired REST endpoint from API_DESIGN.md's knowledge-resource table — a one-line pass-through to `agents.service.list_gap_reports` (File 111), added to the router File 103 already introduced.
- **Depends on:** Files 103, 111.

### 114. `tests/agents/knowledge_gap/test_clustering.py`, `test_pipeline.py`
- **Read now because:** these are worth reading as a model for testing non-deterministic-feeling AI logic deterministically — clustering tests hand-compute exact expected cluster assignments (including an exact-similarity-tie case proving the tie-break rule is `>`, not `>=`) rather than asserting loose bounds; pipeline tests fake the LLM/embedding/retrieval boundaries entirely so the merge-vs-create and action-resolution branches are exercised without any real model call.
- **Depends on:** Files 107, 109.

### 115. `tests/agents/test_service.py`'s Milestone 9 additions
- **You'll learn:** `detect_knowledge_gaps` tested for both the success path (records `succeeded`, returns mapped `GapReport`s) and the failure path (records `failed`, re-raises); `list_gap_reports` tested for both the permission-denied path and the reviewer-success path — the same monkeypatch-the-module-object style as this file's Milestone 8 tests.
- **Depends on:** File 111.

### 116. `tests/api/test_knowledge_router.py`'s `GET /knowledge/gaps` addition
- **Depends on:** Files 104, 113.
- **Unlocks:** nothing further; closes Stage W's Knowledge Gap Agent arc. Milestone 9's remaining connectors (Jira, Teams, Azure DevOps, Confluence, SharePoint, and a sixth `runbooks` connector re-embedding approved postmortems) were built immediately afterward, in `app/ingestion/connectors/{jira,teams,azure_devops,confluence,sharepoint,runbooks}.py` — read those files and their `tests/ingestion/connectors/test_*.py` counterparts directly for now; this roadmap's own dedicated stage for them is a documentation gap still open as of this revision, not a code one. The `runbooks` connector is worth reading closely: unlike every other connector, its source is internal (this codebase's own `postmortems` table via `core.incidents`, not an external SaaS API), which forces it to work around the `Connector` protocol having no `AsyncSession` parameter — its own module docstring explains the workaround (a self-managed `session_scope()` read inside `fetch_batch`) and why it's safe.

---

## Stage X — Milestone 9's Remaining Connectors (Files 117–123)

Five external-SaaS connectors plus one structurally different internal connector, all built immediately after Stage W's Knowledge Gap Agent — this is the documentation gap Stage W's own File 116 flagged as still open, now closed. Read these back-to-back against `github.py`/`slack.py` (Stage J, Files 49–50): every one of them either reuses an established idiom outright or deliberately breaks from it for a documented reason — that's the throughline worth tracking across this stage.

### 117. `app/ingestion/connectors/jira.py`
- **Read now because:** the first of the five remaining connectors, and it sets three patterns the rest either reuse or contrast against: reading a tenant-specific `base_url` from `config.config` instead of a hardcoded API constant (unlike Slack's/GitHub's fixed base URLs), building Basic auth from a literal `"<email>:<api_token>"` credential shape (reused verbatim by `confluence.py`, adapted by `azure_devops.py`), and — originally — deliberately using REST **v2** over v3 so `description` came back as a plain string instead of Atlassian Document Format. **2026 update, a real production bug found and fixed via `scripts/live_connector_tests/test_jira_live.py`'s real network call**: Atlassian permanently removed `/rest/api/2/search` (deprecated 2025-05-01, fully shut down by 2025-10-31 — every request now returns a real `410 Gone`, confirmed live, not from documentation alone). The search call had to move to `/rest/api/3/search/jql`, Atlassian's only replacement — which, confirmed via Atlassian's own bug tracker, only ever returns `description` as ADF, with no plain-text option. The fix: search (v3) is now used ONLY to run the JQL query and pull non-rich-text fields (summary/type/status/people/dates/comment count); a NEW second real call per matched issue, `GET /rest/api/2/issue/{key}` (a completely different, still-supported endpoint family, unaffected by the search deprecation), fetches `description` in plain text separately. One extra HTTP call per issue that didn't exist before — a direct, disclosed cost of Atlassian's removal, not an optional extra.
- **You'll learn:** `_JiraClient` bundling `http`+`projects`+`base_url`; the cursor envelope — now `{"project_index": int, "next_page_token": str | None}` (was `{"project_index", "start_at"}` before the 2026 fix; the new v3 search endpoint paginates via `nextPageToken`, not `startAt`/`total`, so `total`-based exhaustion no longer exists) — Jira's search endpoint only ever searches one JQL query at a time, so a `connector_config` listing multiple project keys needs a "resume mid-list" envelope, the shape every later multi-container connector in this stage reuses; `since` compiled into a real server-side JQL `updated >= "..."` clause; `_SEARCH_FIELDS` (renamed from `_FIELDS` in the 2026 fix, and now deliberately excludes `description`) narrowing exactly which fields the search call fetches; comments ARE fetched per issue via `_fetch_comments_text` (`GET /issue/{key}/comment`, skipped when `comment.total == 0`) — note this contradicts what an earlier draft of this very roadmap entry claimed ("comments are not fetched at all in this pass"); that claim was already stale before the 2026 fix and is corrected here; `normalize()` reconstructs `source_url` from `raw_item["self"]` (Jira's own REST self-link) via a host/path swap rather than threading `base_url` onto the raw item.
- **Depends on:** File 48 (`Connector` protocol), File 50 (`GitHubConnector`'s dataclass-client-bundle + JSON-cursor-envelope idioms, reused here).
- **Unlocks:** Files 118–121 (each either reuses or explicitly contrasts against this file's tenant-base-url/Basic-auth/JQL choices), and File 122's contrast case (external SaaS vs. internal DB source). Also unlocks `scripts/live_connector_tests/test_jira_live.py` (a later, standalone addition, not part of Files 117–123's original numbering) as the reason this 2026 fix exists at all — a real live-network test catching a real, dated external API removal that every existing mocked unit test (File 123) was structurally blind to.

### 118. `app/ingestion/connectors/teams.py`
- **Read now because:** the closest sibling to `slack.py` (a chat source, one `RawDocument` per message — `"teams"` was in fact already anticipated in `app.ingestion.processors.chunking._CHAT_SOURCES` before this connector existed), but it's really the connector that establishes the "Graph API has no `since`/`$filter` support" gap `sharepoint.py` (File 121) hits again later.
- **You'll learn:** `_TeamsClient` bundling `team_id`+`channels`; the `{"channel_index": int, "next_link": str | None}` cursor, where `next_link` is Graph's own full `@odata.nextLink` URL carried opaquely (a meaningfully different, larger cursor shape than Jira's/Azure DevOps'/Confluence's own small native envelopes — reused identically by `sharepoint.py`); `_is_recent_enough`'s client-side `since` filter, needed because Graph's "list channel messages" endpoint has no server-side incremental-sync support the way Slack's `oldest` param or Jira's JQL clause do (Graph's *delta query* API would be the correct long-term fix but is a meaningfully different, cross-sync-persisted pagination model this first pass doesn't implement); no dedicated "verify token" endpoint, so `authenticate` calls `GET /me` as a cheap stand-in for Slack's `auth.test`/Jira's `myself`.
- **Depends on:** File 48, File 49 (`SlackConnector`'s chat-source shape), File 117 (docstring/pattern conventions this file follows).
- **Unlocks:** File 121 (`SharePointConnector` reuses both this file's `next_link` cursor shape and its client-side `since` fallback verbatim).

### 119. `app/ingestion/connectors/azure_devops.py`
- **Read now because:** the only connector in this stage built around a query *language* other than JQL/CQL (WIQL) and the only one whose primary calls are POSTs rather than GETs — both real structural departures worth noticing before moving on to `confluence.py`, which returns to the JQL-like pattern.
- **You'll learn:** `_AzureDevOpsClient` bundling `organization`+`projects`; the `{"project_index": int, "batch_start": int}` cursor, where `batch_start` indexes into the *current* WIQL result (re-fetched fresh, ID-only, on every call — explicitly mirroring `GitHubConnector._list_tree_page`'s "cheap to re-fetch the whole listing every page" precedent, just applied to WIQL's list-IDs-then-batch-fetch-fields shape instead of a git tree); the two-phase-per-project fetch (`_query_work_item_ids` then `_fetch_work_items` via `workitemsbatch`, `_BATCH_SIZE=200`); `since` as a real server-side WIQL `[System.ChangedDate] >= '...'` filter (no client-side fallback needed here, unlike Teams'/SharePoint's Graph gap); `normalize()` builds `source_url` manually from `organization`/`project`/`id` because `raw_item["url"]` is the API URL, not a browse link — a harder case than Jira's `self`-link host/path swap, which is why both `_project` **and** `_organization` get injected onto the raw item.
- **Depends on:** File 48, File 117 (WIQL-vs-JQL parallel), File 50 (`GitHubConnector`'s "cheap to refetch a full listing" idiom).
- **Unlocks:** File 120 (`ConfluenceConnector`'s CQL choice reads as the same "use the real query language when one exists" decision Jira/WIQL already made).

### 120. `app/ingestion/connectors/confluence.py`
- **Read now because:** pairs directly with `jira.py` — same Atlassian Cloud tenant `base_url` + Basic-auth-with-API-token credential shape — but is worth reading for where it *diverges*: CQL over a simpler endpoint, and a narrower content scope (pages only).
- **You'll learn:** `_ConfluenceClient` bundling `spaces`+`base_url`; the `{"space_index": int, "start": int}` cursor; deliberately using `/content/search` with a CQL filter instead of the simpler `/content?spaceKey=...` endpoint, specifically because CQL supports a real server-side `lastmodified >= ...` clause (the same "use the real query language when one exists" choice Jira makes with JQL) where the simpler endpoint has no filter at all; content fetched as Confluence *storage format* (`body.storage.value`, XHTML-like, not Atlassian Document Format) — stripped generically downstream by `processors.cleaning.clean_content`, not by this connector itself; a length-based page-exhaustion heuristic (`len(results) < _SEARCH_PAGE_SIZE`) because CQL search responses don't reliably return a `totalSize` field, explicitly citing `GitHubConnector._list_changed_paths_page`'s identical heuristic as precedent; only Confluence *pages* are fetched (`type = "page"` in the CQL) — blog posts, comments, and attachments are an out-of-scope, flagged gap; `_base_url` is injected onto each raw item (unlike `jira.py`) because Confluence's own `_links.webui` is only a relative path, not a full URL.
- **Depends on:** File 48, File 117 (parallel Atlassian API/credential precedent), File 50 (length-heuristic precedent).
- **Unlocks:** nothing further; reinforces rather than extends the pattern (the same role `github.py` plays after `slack.py` in Stage J).

### 121. `app/ingestion/connectors/sharepoint.py`
- **Read now because:** the last of Milestone 9's five external connectors, and the one with the most consequential disclosed gap in this whole stage — its delta-sync token is never persisted *across* separate sync runs at all, not just across pages of one sync.
- **You'll learn:** `_SharePointClient` bundling `site_ids`; the `{"site_index": int, "next_link": str | None}` cursor, reusing `teams.py`'s "Graph's own opaque absolute nextLink as the cursor" shape verbatim; Graph's drive **delta** endpoint (`/sites/{id}/drive/root/delta`) walks a site's whole document library recursively in one flat paginated walk, unlike a plain `/children` listing that only returns one folder level at a time; the real gap — delta sync is *meant* to be driven by a persisted `@odata.deltaLink` token across syncs, but the `Connector` protocol's `cursor` only lives for one `fetch_batch` sequence, so every sync (full **or** incremental) re-walks the entire delta from scratch — a strictly worse-scoped version of `GitHubConnector`'s already-accepted "full sync is expensive" tradeoff, since here it applies to *every* sync, not just the first; `since` applied as a client-side filter on `lastModifiedDateTime`, the same fallback `teams.py` already uses and for the same underlying reason (no `$filter` support on this endpoint); only `.txt`/`.md`/`.markdown` files get their content fetched — Office documents, PDFs, and images are listed but skipped, not erroring, the same "skipped, not an error" treatment `GitHubConnector._fetch_file_content` gives binary/undecodable files, with the gap explicitly attributed to no `python-docx`/`pypdf` dependency existing yet; the content-download I/O happens inside `fetch_batch` itself (not `normalize`, which is sync-only) — the same shape `GitHubConnector._list_file_items_page` already uses for its own per-file content fetch.
- **Depends on:** File 48, File 118 (`TeamsConnector`'s Graph API/bearer-token/`next_link`-cursor precedent, reused directly), File 50 (`GitHubConnector`'s skip-unsupported-content precedent).
- **Unlocks:** nothing further; last of the five external connectors — File 122 (`runbooks.py`) is the structural odd one out that follows.

### 122. `app/ingestion/connectors/runbooks.py`
- **Read now because:** structurally unlike every other connector in this package — its source is *internal* (this codebase's own `postmortems` table via `core.incidents`), not an external SaaS API — which forces a real, disclosed workaround for the `Connector` protocol having nowhere to thread an `AsyncSession` through. It also closes a gap `agents.investigation.evidence`'s own docstring already flags: "no 'postmortems' retrieval collection exists yet," meaning an approved postmortem was previously only reachable via the narrow, non-ranked `core.incidents.service.list_recent_postmortems` query, never through ordinary hybrid search.
- **You'll learn:** the module docstring's two named architectural gaps — (1) `fetch_batch` opens its own short-lived read session via `app.database.session.session_scope` rather than receiving one, safe specifically because this connector only *reads* (every write this sync produces still goes through `ingestion.service._execute_ingestion_job`'s normal outer-session/savepoint machinery, untouched); (2) `ingestion` importing `core.incidents` is a new, undocumented dependency edge, explicitly paralleled against the already-flagged `ingestion -> core.tenancy` edge, and explicitly noted as *not* caught by `pyproject.toml`'s import-linter contract (which only names `agents`/`mcp` as forbidden for `ingestion`). `_RunbooksClient` bundles just `organization_id`+`actor` — there's no real external client, so `authenticate` makes **no network call at all** (unique among all six connectors, and unique among all connectors in the codebase); `requests_per_second = 100.0` is a nominal placeholder since no real rate limit applies to a local DB read; the `{"offset": int}` cursor is the only bare offset-pagination scheme in this stage (no per-container index, since there's only one virtual container — the org's postmortems); `normalize()` synthesizes `content` from `root_cause` + `action_items` because a postmortem has no single free-text body field the way a Jira issue's description or a Confluence page's storage body does — this is the connector's own interpretive step, still `normalize`'s job per PROJECT_PLAN.md section 4.1, not the pipeline's; ingested postmortems land in the same `documentation` collection `core.knowledge.service.publish_document` already uses for published runbooks (no dedicated "postmortems" collection exists); `source_url` is always `None` (no external page to link to); backed by `core.incidents.service.list_postmortems_for_ingestion`, itself a deliberate no-`actor` exception — same "scheduled system job, no per-request human identity" reasoning as `core.tenancy.service.list_organizations` (Stage W, File 110) — scoped to `statuses=("approved", "published")` only.
- **Depends on:** File 48 (the protocol it partially works around), `app.database.session.session_scope`, `core.incidents.service.list_postmortems_for_ingestion` and its `Postmortem`/`ActionItem` schemas, the gap `agents.investigation.evidence` and File 111 (Stage W) already flag, and `ingestion.service`'s `_CONNECTOR_REGISTRY` (where it's registered under `RunbooksConnector.source_name`).
- **Unlocks:** the Investigation Agent's evidence-gathering step and the Knowledge Gap Agent's `related_document_id` resolution can now both surface approved postmortems through ordinary retrieval search, citations included — not just through `list_recent_postmortems`'s narrow, unranked query.

### 123. `tests/ingestion/connectors/test_jira.py`, `test_teams.py`, `test_azure_devops.py`, `test_confluence.py`, `test_sharepoint.py`, `test_runbooks.py`
- **Read now because:** five of these six share one `_FakeHttpClient`/`_FakeResponse` style (explicitly modeled on `tests/ingestion/connectors/test_github.py`, no real network access, no new mocking dependency) — worth reading as a set to see where that shared style bends per connector, and where `test_runbooks.py` breaks from it entirely.
- **You'll learn:** every one of the five external-connector test files bypasses `authenticate()` (constructing `_JiraClient`/`_TeamsClient`/`_AzureDevOpsClient`/`_ConfluenceClient`/`_SharePointClient` directly), since `fetch_batch`/`normalize` only ever receive that object back and `authenticate` itself does a real verification network call (`GET myself`/`GET me`/`GET space`/`GET _apis/projects`) that these tests never exercise, success or failure; `test_azure_devops.py`'s fake client is the only one that implements `post()` instead of `get()`, since WIQL + `workitemsbatch` are this connector's only calls; `test_sharepoint.py`'s `_FakeResponse` serves double duty (`.json()` for delta-listing calls, `.text` for file-content-download calls) since which one a given test needs depends on which HTTP call it's standing in for; `test_runbooks.py` is structurally distinct from the other five — there's no fake HTTP client at all, instead `incidents_service.list_postmortems_for_ingestion` and `session_scope` are monkeypatched directly (the same "monkeypatch the module-level dependency" style `tests/agents/test_service.py` already uses), and it's the only one of the six that *does* test `authenticate()` (asserting it makes no network call at all — the one connector where that's actually true) and the only one that tests `close()` (asserting it's a no-op). Coverage across all six converges on the same shape: `normalize()` edge cases (an optional field's absence correctly dropping the corresponding metadata key, e.g. Jira's `assignee`/`reporter`, Teams' `author`, Confluence's `webui` link, SharePoint's `folder_path`/`webUrl`), `fetch_batch` pagination/cursor-advancement/project-or-space-or-channel-or-site exhaustion, and `_decode_cursor` default-vs-parse behavior. Conspicuously *not* tested anywhere across the five external connectors' test files: `authenticate()`'s real verification-call failure path (an invalid token or misconfigured `base_url` correctly raising) and `close()` — both are only exercised, trivially, by `test_runbooks.py`, because that connector's version of each is a no-op.
- **Depends on:** File 48, Files 117–122.
- **Unlocks:** nothing further; completes the picture of Milestone 9's ingestion-connector test coverage — and its gaps.

**Surprising findings worth flagging separately:** `runbooks.py`'s `authenticate` makes no network call at all — unique across every connector in the codebase, and the reason its `requests_per_second = 100.0` is a nominal placeholder rather than a real ceiling. SharePoint's delta-sync gap is strictly worse than GitHub's already-accepted "full sync is expensive" tradeoff: GitHub's full-tree walk cost is paid once (full syncs only); SharePoint's un-persisted `deltaLink` means the *entire* delta history is re-walked on **every** sync, incremental included, because `Connector.cursor` has no concept of state surviving past one `fetch_batch` sequence. Azure DevOps and `runbooks.py` are the only two connectors using something other than a straightforward GET-based REST cursor. And test coverage has a real, symmetric gap: none of the five external connectors' test suites exercise `authenticate()`'s or `close()`'s actual behavior — every test bypasses `authenticate` by constructing the private `_XClient` dataclass directly; only `runbooks.py`'s tests touch either method, and only because both are no-ops there. Also worth knowing: `ingestion` importing `core.incidents` (for `runbooks.py`) is a documented-but-unenforced dependency edge — `pyproject.toml`'s import-linter contract for `ingestion` only names `agents`/`mcp` as forbidden, so this new edge is a real, self-disclosed gap in the architecture rules rather than something the tooling would catch.

---

## Stage Y — Milestone 10: Production Hardening (Files 124–136)

Every prior stage described a functionally complete but operationally naive system: plaintext credentials in the database, an unthrottled ingestion worker, no read surface on the two observability tables built back in Stages C/R, and tenant isolation enforced *only* in application code despite `PROJECT_PLAN.md` section 3.7 listing Postgres RLS as a distinct layer. This stage closes all four gaps, plus confirms the MCP SDK version Stage S flagged as unverified. Read it in this order: encryption before its use in tenancy/ingestion, rate limiting next (it shares the same `ingestion.service` call site encryption just modified), observability third (small, self-contained), RLS as the largest and most cross-cutting piece (migration → session plumbing → chokepoints → tests), then the MCP SDK port on its own, then the two Milestone 10 docs last as a retrospective — mirroring Stage AA's own "read the code, then the doc that explains it" ordering.

### 124. `app/shared/security/kms.py` and `app/shared/security/envelope.py`
- **Read now because:** this is the new cross-module primitive both `core/tenancy` and `app/ingestion` are about to call — read the abstraction before either caller, the same "protocol before implementations" order Files 41–42/48/77a already established.
- **You'll learn:** the `KeyManagementService` Protocol (`generate_data_key`/`decrypt_data_key`, wrapping/unwrapping a per-secret DEK with a KEK the protocol never exposes to callers) and `LocalKeyManagementService`, its one, explicitly-flagged-as-pre-production implementation — holds its KEK as a plain `Settings.connector_secret_master_key` value rather than in a real hardware-backed trust boundary, meaning (unlike the target property PROJECT_PLAN.md section 12.5 describes) a compromise of this app's own runtime would be sufficient to decrypt every stored credential; swapping in a real cloud KMS means writing one new class satisfying this same protocol, no caller changes. `envelope.py`'s `encrypt_secret`/`decrypt_secret` are pure functions over that protocol: AES-256-GCM, a fresh random DEK generated per secret (`os.urandom(32)`) so one compromised DEK never exposes another secret, serialized as a versioned JSON envelope (`{"v": 1, "encrypted_dek", "nonce", "ciphertext"}`, all base64) — the `"v"` tag exists from day one specifically because retrofitting a version field onto already-stored ciphertext later would be expensive. `decrypt_secret` deliberately does not catch `InvalidTag`/`ValueError`/`JSONDecodeError` — a caller getting garbage back instead of a real credential is worse than a loud failure.
- **Depends on:** File 7 (`get_settings().connector_secret_master_key`).
- **Unlocks:** File 125.

### 125. `app/shared/security/__init__.py`
- **Read now because:** it's short, and its docstring is the one place that states *why* this package lives in `shared/` rather than under `core/tenancy` or `app/ingestion` — a genuinely cross-module concern neither owns.
- **You'll learn:** the package's re-export surface (`encrypt_secret`/`decrypt_secret`/`get_kms`/`KeyManagementService`/`LocalKeyManagementService`), and a deliberate, disclosed simplification worth remembering: `PROJECT_PLAN.md` section 12.5's prose implies a dedicated secrets-record table separate from `connector_configs`, but this codebase has none — `register_connector` (File 126) stores the full envelope blob directly in the existing `connector_configs.credential_ref` `Text` column instead of adding a new table/migration for this pass. Section 12.5's actual safety property ("the database only ever stores the encrypted secret and the encrypted DEK, never a usable plaintext credential or the KEK") still holds in full; only the extra indirection layer the prose's wording implies is skipped, flagged here as a reasonable future refinement (e.g. for secret rotation independent of `connector_configs` rows) rather than silently assumed away.
- **Depends on:** File 124.
- **Unlocks:** File 126.

### 126. `app/core/tenancy/service.py`'s `register_connector` (Milestone 10 addition) and `app/ingestion/service.py`'s `_execute_ingestion_job` (Milestone 10 addition)
- **Read now because:** this is encryption's only encrypt-at-write/decrypt-at-read pair in the whole codebase — read both call sites together, right after the primitive, rather than rediscovering the second one later out of context.
- **You'll learn:** `register_connector` (File 28, revisited) now calls `encrypt_secret(get_kms(), data.credential_ref)` before persisting a connector's plaintext credential — the caller's plaintext value is never logged or written to `connector_configs` directly, only the resulting envelope is. `_execute_ingestion_job` (File 56, revisited) calls `decrypt_secret(get_kms(), config_row.credential_ref)` exactly once per job, immediately before `connector.authenticate()` needs it, into the `ResolvedConnectorConfig` handed to the connector — this is the one place in the entire ingestion path a plaintext credential exists at all, and it's never persisted or held longer than that function's own local variables live.
- **Depends on:** Files 28, 56, 124–125.
- **Unlocks:** confidence that every Milestone 9 connector's (Files 49–50, 117–122) `authenticate()` call receives a real, decrypted credential without any change to their own code.

### 127. `app/ingestion/rate_limiter.py`
- **Read now because:** it shares `_execute_ingestion_job`'s fetch loop with the encryption change you just read, and closes a gap `ingestion.workers.tasks.scheduled_reconciliation`'s own docstring used to flag explicitly ("each enqueued job is independently rate-limited per connector_config... not attempted here").
- **You'll learn:** `TokenBucketRateLimiter`, a dependency-free async token bucket keyed by an arbitrary string, burst capacity equal to the rate itself; `acquire(key, rate)` treats `rate <= 0` as "no limit" rather than deadlocking or dividing by zero. Two independent budgets are acquired before every `fetch_batch` call: a per-`connector_config_id` bucket at that connector's own declared `requests_per_second` (`Connector.requests_per_second`, File 48), and a per-organization bucket at a fixed aggregate cap (`Settings.ingestion_org_max_requests_per_second`, default 5.0) — the "per tenant" half a purely per-connector limiter would miss, since one organization's Jira+Confluence+GitHub connectors all syncing at once could otherwise collectively exceed a shared budget while each individually stays under its own ceiling.
- **Note:** two limitations are disclosed in the module docstring, not silently assumed away: (1) this is an **in-process** limiter — a module-level dict, not Redis-backed — so multiple concurrent arq worker *processes* would each enforce their own independent view of the same budget, effectively multiplying the real ceiling by the process count; a Redis-backed distributed bucket is flagged as the correct production fix. (2) one `fetch_batch` call can itself issue more than one real HTTP request internally (e.g. GitHub's per-file content fetch, Azure DevOps's WIQL-then-batch-fetch pair), so "one token per `fetch_batch` call" approximates requests/second rather than throttling every individual HTTP call.
- **Depends on:** File 48 (`Connector.requests_per_second`), File 7 (`ingestion_org_max_requests_per_second`).
- **Unlocks:** `tests/ingestion/test_rate_limiter.py` (File 133 below).

### 128. `app/api/routers/observability.py`, `app/core/observability/service.py`'s `get_mcp_dashboard`, `app/agents/service.py`'s `get_agent_execution_stats`
- **Read now because:** this is the first REST-facing read surface either `agent_executions` (File 19) or `mcp_requests` (File 20) has ever had — small, self-contained, and a good example of the "thin router, real logic in `service.py`" convention holding under a genuinely new kind of endpoint (an aggregate dashboard, not a CRUD resource).
- **You'll learn:** `GET /observability/agents` → `agents.service.get_agent_execution_stats(session, actor, since=None)`, gated by `observability:read`, aggregating `agent_executions` by `agent_name` (`agents.repository.get_agent_execution_stats` — execution count, succeeded/failed counts, average confidence, average latency) and org-scoped to `actor.organization_id`. `GET /observability/mcp` → `core.observability.service.get_mcp_dashboard`, same permission, aggregating `mcp_requests` by `tool_name` (request count, error count — a `NULL` or `>= 400` `status_code` both count as an error — average/max latency) — deliberately **not** organization-scoped, unlike every other router in `app/api`, because `mcp_requests` (File 20) carries no `organization_id` column at all; every caller with the permission sees the same platform-wide aggregate. Both take an optional `since: datetime | None` query parameter, defaulting to "all time."
- **Depends on:** Files 19–20, 60, 86–88, 103.
- **Unlocks:** `tests/api/test_observability_router.py`.

### 129. `app/database/migrations/versions/c7d4e8f19a2b_milestone_10_row_level_security.py`
- **Read now because:** this is the actual database-level backstop `EKIP_TENANT_ISOLATION_SECURITY_REVIEW.md` (File 135) names as the one real finding of its review — read the migration itself before any of the application-code plumbing that depends on it, the same "schema before service" ordering Stage C established.
- **You'll learn:** every table carrying `organization_id` gets `ENABLE ROW LEVEL SECURITY` **and** `FORCE ROW LEVEL SECURITY` (the latter so the policy also binds the table-owner role this application's own connection pool uses, not just other roles — though an actual Postgres *superuser* still bypasses RLS regardless of FORCE, meaning the deployed app DB role must not be one, a caveat carried into File 135) plus one bare `CREATE POLICY tenant_isolation ... USING (organization_id = current_setting('app.current_organization_id', true)::uuid)` — no `FOR SELECT`/`FOR INSERT` scoping, so the same expression governs both "which existing rows are visible" and (absent a separate `WITH CHECK`) "is this new/updated row allowed," closing off write-side leaks too, not just reads. `current_setting(..., true)`'s `missing_ok` argument returns `NULL` rather than erroring if the GUC was never set — and `organization_id = NULL` evaluates to `NULL` (falsy) for every row, so a connection that forgot to set this variable sees **zero** rows, never every row: deliberately fail-closed. Two tables (`document_metadata`, `project_memberships`) get a subquery-based policy instead, since neither carries its own `organization_id` column (scoped via `document_id`/`project_id` FK instead). `organizations`, `users`/`roles`/`permissions`/`role_permissions`, and `mcp_requests` are deliberately excluded — either genuinely global, org-less by design, or carrying no `organization_id` at all.
- **Depends on:** Files 14–20 (every table this migration touches).
- **Unlocks:** File 130.

### 130. `app/database/migrations/versions/d2e5f8a3c1b6_milestone_10_rls_bypass_functions.py`
- **Read now because:** this is the direct answer to the "chicken-and-egg" problem the previous migration creates — read immediately after it, since nothing about these four functions makes sense without first understanding why a connection with no GUC set sees zero rows.
- **You'll learn:** four narrow `SECURITY DEFINER` SQL functions, each returning only an id (never a full row) and each pinning `SET search_path = public` (hardening against a well-known `SECURITY DEFINER` footgun — a hostile caller `search_path` re-pointing which same-named object the function resolves): `resolve_connector_config_organization(config_id)`, `resolve_document_organization(doc_id)`, `resolve_refresh_token_organization(token_hash_arg)`, and `list_active_connector_config_ids()` (a genuinely cross-tenant enumeration, not a single-row resolution — the RLS-protected sibling of `core.tenancy.repository.list_organizations`, File 110). Each exists for exactly one caller that must discover a row's own `organization_id` *before* `set_tenant_context` (File 131) can be called at all, since that row is itself what RLS would otherwise hide: `ingestion.service._execute_ingestion_job`'s and `reindex`'s bare-PK first reads, `ingestion.workers.tasks.scheduled_reconciliation`'s cross-tenant scan, and `core.auth.service.refresh`/`logout`'s bare-token-hash lookups. Deliberately narrowed to "which org owns this one id" rather than granting the app's connection role a blanket `BYPASSRLS` attribute — PROJECT_PLAN.md section 12.8's least-privilege value, never a reusable trapdoor.
- **Depends on:** File 129.
- **Unlocks:** File 131.

### 131. `app/database/session.py`'s `set_tenant_context` (Milestone 10 addition)
- **Read now because:** you've now seen both what the GUC does (File 129) and why some callers can't set it immediately (File 130) — read the actual setter next, in full, since nearly every chokepoint file after this one is just "call this, in the right order."
- **You'll learn:** `set_tenant_context(session, organization_id)` calls `set_config('app.current_organization_id', str(organization_id), true)` — a plain function call taking a bound parameter, deliberately not a literal `SET LOCAL ...` string built by interpolation, since `SET` itself doesn't accept bind parameters and this would otherwise be the one place in the codebase doing raw string interpolation into SQL. The third argument (`true`, i.e. `is_local`) is what scopes this to the *current transaction only* (cleared on commit/rollback) rather than the whole pooled connection, which matters because sessions aren't guaranteed to map 1:1 onto connections — a "session"-scoped set would otherwise leak forward into whatever unrelated request/job happens to reuse that connection next.
- **Depends on:** File 12 (`AsyncSession`).
- **Unlocks:** File 132 — every chokepoint that calls this function.

### 132. Every `set_tenant_context` chokepoint: `app/api/deps.py`, `app/mcp/dispatch.py` + `app/mcp/servers/server.py`, `app/core/users/service.py`'s `resolve_identity`, `app/core/tenancy/service.py`'s `get_organization_sso_config`/`evaluate_provisioning`, `app/core/auth/service.py`'s `refresh`/`logout` + `app/core/auth/repository.py`'s `resolve_refresh_token_organization_id`, `app/ingestion/service.py`'s `_execute_ingestion_job`/`reindex` + `app/ingestion/repository.py`'s matching bypass wrappers, `app/ingestion/workers/tasks.py`'s `scheduled_reconciliation`, `app/agents/workers/tasks.py`'s `run_knowledge_gap_detection_task`
- **Read now because:** this is the complete, traced set of places `Identity`/org context becomes known in this codebase — read them together as one pass, since they're one design decided once and copied to every entry point, not eleven independent decisions.
- **You'll learn:** the ordering rule every one of these follows — `set_tenant_context` must run before the *first* RLS-protected query on that session, and the sequence in which context becomes available differs by entry point. REST: `api.deps.get_current_identity` calls it right after `users_service.resolve_identity` returns. MCP: `mcp.dispatch.run_mcp_tool` calls it the same way, through the injected `server_module.set_tenant_context` callable (Files 91/93's dependency-inversion trick, since `app.mcp` may still never import `app.database` — File 131's setter is wired in at process start by `scripts/run_mcp_server.py`, alongside `session_factory`). Pre-Identity, org-known-by-parameter paths: `core.tenancy.service.get_organization_sso_config` resolves org-by-slug then calls it immediately; `evaluate_provisioning` receives `organization_id` directly from its caller and sets it unconditionally at the top, before its first `invitations` query — and because `set_tenant_context` uses `SET LOCAL` (transaction-scoped) and SSO login runs `get_organization_sso_config` → `evaluate_provisioning` → `accept_invitation` inside one shared transaction, this single call also covers `accept_invitation`'s later write, no separate wiring needed. Genuinely bare-id/hash, pre-context paths use the File 130 bypass functions first: `ingestion.service._execute_ingestion_job` (bare `connector_config_id`) and `reindex` (bare `document_id`) each resolve org via `repository.resolve_connector_config_organization_id`/`resolve_document_organization_id`, then call `set_tenant_context`, then run the real RLS-scoped read; `core.auth.service.refresh`/`logout` do the identical dance via `repository.resolve_refresh_token_organization_id` against a bare client-presented token hash. Scheduled/background paths with an org id already in hand: `agents.workers.tasks.run_knowledge_gap_detection_task` sets it immediately after opening its session, before its one `detect_knowledge_gaps` call.
- **Note — a real ordering bug found and fixed during this milestone, not a hypothetical:** `core.users.service.resolve_identity` itself queries the RLS-protected `user_roles` table (via `get_role_names`/`get_permission_codes`) to build an `Identity` — and it runs *before* either `api.deps.get_current_identity` or `mcp.auth.resolve_mcp_identity` gets a chance to call `set_tenant_context` (both originally called it only *after* `resolve_identity` already returned). Left as first wired, this would not have raised any error: every single login, on every request, would have silently resolved to an `Identity` with empty `roles`/`permissions` (RLS hiding every `user_roles` row, not just cross-tenant ones) — which fails every `authorize()`/`require_permission()` check closed, locking every user out of everything, invisibly, with nothing to point at. Fixed by moving `set_tenant_context` to the very first line of `resolve_identity` itself (it already receives `organization_id` as a required parameter, so no bypass function was needed here) — the two chokepoints' own later calls are now an intentional, harmless redundancy, not the only place this happens. This is exactly the class of subtle mistake `EKIP_TENANT_ISOLATION_SECURITY_REVIEW.md`'s final recommendation (re-run this review after future milestones) exists to catch before it reaches this severity again. This is also the file you'll want to re-read once you reach Stage Z's File 137 — that stage's `get_project_permission_map` addition is a fourth query inside this same, now-well-understood function.
- **Depends on:** Files 28, 31, 34, 56, 89–90, 112, 129–131.
- **Unlocks:** File 133 — the tests that pin every one of these orderings down.

### 133. `tests/database/test_session.py`, `tests/api/test_deps.py`, `tests/mcp/test_dispatch.py`, `tests/core/users/test_service.py`, `tests/core/tenancy/test_service.py`, `tests/core/auth/test_service.py`, `tests/ingestion/test_repository.py`, `tests/ingestion/test_service.py`, `tests/agents/workers/test_tasks.py`, `tests/ingestion/test_rate_limiter.py`, `tests/shared/security/test_kms.py` + `test_envelope.py`
- **Read now because:** these are worth reading as a set precisely because none of them run against a real Postgres instance (see File 135's disclosed caveat) — every RLS-ordering claim in File 132 is instead pinned down via monkeypatched fakes recording call order, and it's worth seeing exactly how much confidence that style of test can and can't provide before trusting it.
- **You'll learn:** `test_session.py` asserts `set_tenant_context` calls `set_config` with bound parameters (not string interpolation) and stringifies the UUID. Every chokepoint test follows the same shape: a `call_order: list[str]` list, a monkeypatched `fake_set_tenant_context` that appends to it, and an assertion like `call_order.index("set_tenant_context") < call_order.index("get_role_names")` — proving *ordering*, not actual RLS enforcement (which needs a live database this test suite doesn't have). `test_ingestion/test_repository.py` and `test_service.py` additionally verify the bypass-function call sites (`resolve_connector_config_organization_id`/`resolve_document_organization_id`/`list_active_connector_config_ids`) produce the right SQL function names. `test_rate_limiter.py` verifies burst-then-throttle timing behavior and the `rate <= 0` no-op case. `test_kms.py`/`test_envelope.py` verify the AES-256-GCM round trip, the versioned-JSON envelope shape, and that encrypting the same secret twice produces different ciphertext (fresh DEK/nonce per call). **Note for when you reach Stage Z:** `tests/core/users/test_service.py` is revisited a second time there — a shared `_patch_resolve_identity_dependencies` helper is added specifically so the integration-gaps pass's `get_project_permission_map` addition doesn't require re-deriving this file's monkeypatch plumbing from scratch.
- **Depends on:** Files 124–132.
- **Unlocks:** nothing further in this stage; closes the RLS/encryption/rate-limiting testing arc.

### 134. `app/mcp/servers/server.py` (Milestone 10 revision) and `scripts/run_mcp_server.py` (Milestone 10 revision)
- **Supersedes File 91's original entry above:** Stage S flagged this file's transport choice and header-extraction approach as "unverified against the actually-installed `mcp` package" — that verification has now happened, and it turned out the pinned `mcp>=1.0` requirement was stale. The environment this project actually runs in has **`mcp==2.0.0`** installed, a genuine major-version break from the 1.x `FastMCP` API File 91 was originally written against (confirmed by direct inspection of the installed package's source, not guessed): there is no `mcp.server.fastmcp` module in 2.0 at all — the equivalent class is `mcp.server.mcpserver.MCPServer` (renamed from `FastMCP`), alongside `mcp.server.mcpserver.Context` (unchanged name); the constructor and `@mcp_server.tool()`/`.resource()`/`.prompt()` decorators kept the same shape, so nothing in `mcp/tools/`/`mcp/resources/` needed to change beyond their `Context` import path. `extract_bearer_token` now reads `ctx.headers` (a public, documented `Context` property in 2.0) instead of reaching into the old private `ctx.request_context.request.headers` shape. This same file also gained the `set_tenant_context` injected-callable seam (mirroring `session_factory`'s existing dependency-inversion pattern, since `app.mcp` still may never import `app.database`) — `scripts/run_mcp_server.py` now wires both `session_factory = session_scope` and `set_tenant_context = set_tenant_context` at startup, before serving any request.
- **You'll learn:** exactly what changed in the SDK port (above) and what didn't (the tool/resource/prompt registration API, unchanged from Files 100–101 — and still unchanged again by Stage Z's four new tools, File 143).
- **Depends on:** Files 90–91, 93, 102, 131.
- **Unlocks:** a resolved reading of Stage S's one open flag — nothing about the MCP layer's design needs revisiting, only this one version-specific detail.
- **Note:** `pyproject.toml`'s dependency line (checked directly) still literally reads `"mcp>=1.0"` — the code was ported to the 2.0 API but the declared version constraint was not tightened to match (e.g. `mcp>=2.0,<3.0`), a small, real inconsistency worth knowing about if you ever `pip install` this project fresh and get a 1.x `mcp` instead.

### 135. `EKIP_TENANT_ISOLATION_SECURITY_REVIEW.md`
- **Read now because:** this is the Milestone 10 deliverable that motivated Files 129–133 — read it *after* the code, not before, so every claim in it reads as confirmation of something you already traced yourself rather than an assertion to take on faith.
- **You'll learn:** a section-by-section audit against `PROJECT_PLAN.md` section 3.7's seven tenant-isolation enforcement layers (login token scoping, application query scoping, Postgres RLS, vector search hard filter, background job scoping, audit log scoping, MCP token scoping) — six passed on the original review pass; RLS (the seventh) is the one gap the review found, and the document was **updated in place** (not left stale) once Files 129–132 closed it, including the `resolve_identity` ordering bug called out in File 132's note. Also documents a precision correction to the project's own claim ("no tenant-owned table is queryable without a tenant context" is true at the service layer via post-fetch `_ensure_same_organization`-style ownership checks, not literally true at the repository layer, where a number of PK-only lookups exist by design) and one disclosed, not-fully-closed gap of its own: `core/auth/service.py`'s and `core/users/service.py`'s remaining PK-based lookups were not individually re-traced in this pass.
- **Depends on:** Files 124–134 (every piece of code it audits).
- **Unlocks:** File 136.
- **Note — three real, disclosed limitations worth internalizing, not just skimming past:** (1) **none of Milestone 10's RLS work has ever been run against a live Postgres instance** — every claim is unit-tested against fakes/monkeypatches only (the sandbox this was built in had no disk space to start an isolated database); (2) the review's top open recommendation is to **confirm the deployed application's database role is not a superuser/table-owner-bypass role** — `FORCE ROW LEVEL SECURITY` is silently bypassed for such a role regardless, which would make the entire migration a no-op in production; (3) a suggested follow-up lint rule (flagging any new `repository.py` query against a tenant-owned table with no `organization_id` in its `WHERE`, or any new bare-PK/bare-hash lookup that doesn't route through an established chokepoint) does not exist yet — this remains a manual discipline, not an enforced one.

### 136. `docs/USER_TESTING_GUIDE.md`
- **Read now because:** read last in this stage (and, honestly, a strong candidate for reading before Stage AA's `PROJECT_STATUS.md`/`ENGINEERING_DECISIONS.md`, since it is explicitly dated *after* both and says so) — it's the freshest whole-system narrative in the repo, written specifically to be trustworthy where the two Stage AA docs are stale.
- **You'll learn:** a feature checklist confirming Milestone 10 as shipped ("Envelope-encrypted (AES-256-GCM) storage of connector credentials," "Per-connector and per-organization ingestion rate limiting," "Postgres Row-Level Security enforcing tenant isolation at the database layer," "Structured-logging-based observability dashboards"), a request-flow-by-entry-point walkthrough (REST/MCP/ingestion job/scheduled agent) that doubles as a plain-English retelling of File 132's chokepoint list, a full local setup + REST/MCP smoke-test walkthrough, and its own explicit warning that `docs/PROJECT_STATUS.md`/`PROJECT_STRUCTURE.md` describe an early "Phase 1, no implementation yet" state and should be distrusted wherever they conflict with the code — a stronger, more current version of the staleness warning Files 4/147 already gave you. Written before Stage Z's integration-gaps pass, so its own feature checklist does not yet mention the tenancy admin surface, project-scoped RBAC, logout-everywhere, or the monitoring connector — a further, honest staleness gap worth knowing about rather than assuming this doc is exhaustive.
- **Depends on:** essentially everything in Stages A–Y; written as an outside-in tour of the same system.
- **Unlocks:** nothing further; your own ability to actually run and click through the whole system end-to-end, REST and MCP both (as of Milestone 10 — Stage Z's additions came after this doc was last touched).

**Surprising findings worth flagging separately:** a real, previously-live security bug was found and fixed mid-milestone, not a hypothetical — see File 132's note above. None of the RLS work has ever been executed against a real Postgres instance (the review's own top-priority recommendation is to confirm the deployed DB role isn't a superuser/bypass role, since `FORCE ROW LEVEL SECURITY` is silently a no-op for one). `LocalKeyManagementService` is explicitly flagged as not yet delivering the separate-trust-boundary property section 12.5 describes. The rate limiter is in-process, not distributed, and would need a Redis-backed bucket to hold under multiple worker processes. `pyproject.toml` still pins `mcp>=1.0` despite the 2.0-only port. And the envelope-encryption design stores the full encrypted blob directly in `connector_configs.credential_ref` rather than the dedicated secrets table PROJECT_PLAN.md section 12.5's prose implies — a disclosed simplification, not an oversight.

---

## Stage Z — The Integration-Gaps Closure Pass (Files 137–146)

Milestone 10 hardened what already existed; this stage closes a different kind of gap — pieces of `core/`'s public interface that had real, working service-layer functions with no REST or MCP entry point reaching them at all, a permission model (`Identity.project_permissions`) that existed as a field on the `Identity` schema since Stage B but was never actually populated, a live-evidence source (Stage O's File 77d) registered nowhere, and human-review endpoints the knowledge workflow was missing. None of this touches `agents/graph.py`'s routing logic, the retrieval library, or the ingestion pipeline — it is entirely additive REST/MCP surface plus one permission-resolution pipeline wired end-to-end for the first time. Read it in this order: the permission pipeline first (everything else in this stage either enforces against it or is unaffected by it), then the three `core/` service-layer changes that use it or that this pass otherwise touches, then the monitoring-connector wiring (small, self-contained), then the REST/MCP surface additions, then the tests.

### 137. `app/core/users/repository.py`'s `get_project_permission_map` and `app/core/users/service.py`'s `resolve_identity`/`require_project_permission` (Milestone 11 addition — read as a revisit of File 31)
- **Read now because:** `Identity.project_permissions` (File 9) has existed as a typed field since Stage B, and `has_permission(code, project_id=...)` (also File 9) already knew how to *check* it — but nothing ever populated it before this pass; `resolve_identity` silently returned every `Identity` with an empty `project_permissions` dict, so every project-scoped check anyone wrote would have quietly fallen back to org-level permissions only. This closes that gap.
- **You'll learn:** `get_project_permission_map(session, user_id, organization_id)` — a single query joining `project_memberships` → `projects` → `role_permissions` → `permissions`, grouped by `project_id` into a `dict[uuid.UUID, frozenset[str]]`; `resolve_identity` (File 31, revisited) now calls it alongside its existing `get_role_names`/`get_permission_codes` calls and passes the result into `Identity(...)`, completing the pipeline the docstring now describes as `JWT → verify_access_token() → resolve_identity() → project_permissions populated`. `require_project_permission(actor, project_id, permission_code)` is a thin, call-site-clarity wrapper around the existing `require_permission(actor, code, project_id=...)` (File 31) — not a stricter or different check, just a name that reads correctly at call sites that are unambiguously project-scoped (incidents, knowledge, connector registration) rather than the more general org-or-project `require_permission` signature.
- **Depends on:** Files 9, 11, 15 (`ProjectMembership`/`RolePermission`/`Permission`), 31.
- **Unlocks:** Files 138–140 (each swaps in `require_project_permission` at its own call sites), and File 146's test coverage.

### 138. `app/core/tenancy/service.py`'s `create_organization`, `accept_invitation`, and `register_connector` (integration-gaps additions — read as a revisit of File 28)
- **Read now because:** three separate, independent changes to a file you've already read in full — grouped here because they're the direct prerequisites for Stage Z's REST surface (File 144) and its permission enforcement (File 137), not because they share one theme.
- **You'll learn:** `create_organization` gains an optional `actor: Identity | None = None` parameter — omitted, it behaves exactly as before (both of its existing script-based callers, `scripts/seed_test_organization.py` and `scripts/test_milestone6.py`, still pass none and are unaffected); supplied (the new `POST /organizations` REST endpoint always supplies one), it records an `organization.create` audit event attributed to that actor, matching this codebase's existing lowercase-dotted audit-action-naming convention (not the `ORGANIZATION_CREATED`-style naming an earlier draft of this pass's own spec called for — see this stage's closing note on deliberate deviations). `accept_invitation` gains real existence/status/expiry guards it previously lacked (`NotFoundError` for an unknown id, `ConflictError("invitation.not_pending")`/`ConflictError("invitation.expired")` otherwise) — necessary now that a REST caller (File 144's `POST /invitations/{invitation_id}/accept`) can reach this function directly, unauthenticated, rather than only ever being called mid-SSO-login from trusted code that already checked these things implicitly. `register_connector` now branches on `data.project_id`: supplied, it validates the project belongs to the organization and calls `require_project_permission(actor, data.project_id, "tenancy:manage")` (File 137) instead of a bare org-level check; omitted (an org-wide connector), it falls back to the plain `require_permission(actor, "tenancy:manage")` — the first real, reachable exercise of the new project-permission pipeline anywhere in the codebase.
- **Depends on:** Files 28, 137.
- **Unlocks:** File 142 (REST), File 145 (MCP tools), File 146's test coverage.

### 139. `app/core/knowledge/service.py`'s `update_document`, and project-scoped `get_document`/`publish_document`/`reject_document` (integration-gaps additions — read as a revisit of File 98)
- **Read now because:** the human-review half of the knowledge workflow (File 98) previously had no edit path at all — a reviewer could publish or reject a proposal verbatim, never correct a typo in its title or tighten its content first. `propose_runbook_update` (the MCP tool, File 100) remains the *only* way to create a proposal — this pass deliberately does not add a REST creation path, preserving the "verified evidence vs. AI-generated hypothesis"-style human-in-the-loop boundary AGENT_WORKFLOWS.md already established for documents.
- **You'll learn:** `DocumentUpdate` (title/content, both optional) and `update_document(session, actor, organization_id, document_id, data)` — fetches the document, calls `require_project_permission(actor, row.project_id, "knowledge:review")` (File 137), rejects with `ConflictError("document.not_proposed")` unless `status == "proposed"` (an already-published or -rejected document is immutable, matching the existing state-machine discipline File 98 established), applies `data.model_dump(exclude_unset=True)` so an omitted field is left alone rather than nulled, updates `documents.title` directly or bumps `documents.version` (a content-only edit has nowhere else to record that a change happened, since `content` itself is stored as `document_metadata`, not a column — see File 98's own design note on this), upserts the `content` metadata row via a new `repository.upsert_metadata` (added because `document_metadata` has no unique constraint on `(document_id, key)`, so this function does a select-then-update-or-insert rather than relying on one), and records a `document.update` audit event. `get_document`/`publish_document`/`reject_document` all now check `require_project_permission`/`has_permission(..., project_id=row.project_id)` against the document's own `project_id` instead of a bare org-level `knowledge:review` check — the same project-scoping shape File 138's `register_connector` change applies to connectors.
- **Depends on:** Files 98, 137.
- **Unlocks:** File 144 (REST `GET`/`PATCH /knowledge/{document_id}`), File 146's test coverage.

### 140. `app/core/incidents/service.py`'s permission-check call sites (integration-gaps addition — read as a revisit of File 40)
- **Read now because:** a small, purely clarifying change worth reading quickly right after Files 138–139, while the project-permission pipeline is fresh — the one `core/` module in this pass that already had project-scoped permission checks (`require_permission(actor, code, project_id=...)`, present since File 40 was first written) and only needed its call sites reworded, not redesigned.
- **You'll learn:** `create_incident`, `update_incident`, `add_timeline_note`, and `trigger_postmortem_generation` now call `require_project_permission(actor, project_id, code)` (File 137) instead of `require_permission(actor, code, project_id=project_id)` — behaviorally identical, purely a call-site-clarity rename matching File 137's new wrapper. `update_postmortem`/`approve_postmortem` are deliberately left calling the more general `require_permission` unchanged, since a postmortem's `project_id` can legitimately be `None` in a way the four renamed call sites' inputs cannot.
- **Depends on:** Files 40, 137.
- **Unlocks:** nothing further; closes the loop on every `core/` module this pass's permission pipeline actually reaches (incidents, knowledge, tenancy/connectors) — retrieval and ingestion were deliberately left untouched, see this stage's closing note.

### 141. `app/core/tenancy/schemas.py`'s `ConnectorSource.MONITORING`, `app/shared/schemas/agent_contracts.py`'s `EvidenceItem.source`, and `app/agents/investigation/evidence.py`'s `_LIVE_SOURCES` registration (integration-gaps addition — read as a revisit of Files 26, 58, and 77/77d)
- **Read now because:** this is the smallest, most self-contained change in the whole stage — three one-line-feeling edits that together turn `MonitoringLiveSource` (File 77d) from an inert, unreachable stub into a live, dispatchable evidence source, closing the exact gap File 77d's own original entry named: "not actually wired into `evidence._LIVE_SOURCES` — there's no `ConnectorSource` value for 'monitoring'... and no `connector_configs` row a monitoring source could resolve from yet."
- **You'll learn:** `ConnectorSource` (File 26) gains a `"monitoring"` literal value, with an explanatory module-level docstring noting it has no `app.ingestion._CONNECTOR_REGISTRY` entry and never will need one — a `connector_configs` row with `source="monitoring"` is only ever consulted by `agents.investigation.evidence`, never by the ingestion worker, so registering one does not enqueue any ingestion job (verified safe by tracing `ingestion.service`'s registry lookup, which already raises a clean `ConflictError` for any unregistered source rather than crashing); `EvidenceItem.source` (File 58) gains the matching `"monitoring"` literal; `evidence.py`'s `_LIVE_SOURCES` dict (File 77) gains a `"monitoring": MonitoringLiveSource()` entry alongside the existing `"github"`/`"slack"` ones — the only code change `_gather_live_evidence`'s dispatch logic needed, since that function was already written generically against whatever keys `_LIVE_SOURCES` happens to hold.
- **Depends on:** Files 26, 58, 77, 77d.
- **Unlocks:** File 146's regression test (`test_monitoring_connector_source_resolves_to_monitoring_live_source`, asserting against the real module-level `_LIVE_SOURCES` rather than a monkeypatched fake, specifically so it fails if this registration is ever accidentally reverted).

### 142. `app/api/routers/tenancy.py`'s `admin_router` and `app/api/main.py`'s router wiring (integration-gaps addition — read as a revisit of File 103)
- **Read now because:** this is the largest single file in the whole stage — a previously entirely-missing REST surface for most of `core.tenancy.service` (File 28), covering thirteen endpoints across organizations, projects, SSO configuration, access rules, and invitations, none of which had any REST or MCP entry point before this pass.
- **You'll learn:** a second `APIRouter` (`admin_router`, no prefix, distinct from the file's existing `router` at `/tenancy`) exposing `POST`/`GET /organizations`, `GET /organizations/{organization_id}`, `POST`/`GET /organizations/{organization_id}/projects`, `POST /organizations/{organization_id}/sso/configure`, `POST`/`GET /organizations/{organization_id}/access-rules`, `PATCH /access-rules/{rule_id}/deactivate`, `POST`/`GET /organizations/{organization_id}/invitations`, `POST /invitations/{invitation_id}/accept`, and `POST /invitations/{invitation_id}/revoke`. Two deliberate scoping decisions the module docstring states plainly and are worth internalizing rather than assuming away: `GET /organizations` does **not** call the unscoped `core.tenancy.service.list_organizations` (File 110's own deliberate no-actor, cross-tenant, cron-only function) — it returns a single-element list containing only the caller's own organization via `get_organization`, since exposing the real unscoped listing to any authenticated caller would leak every organization in the system; and `POST /invitations/{invitation_id}/accept` is deliberately unauthenticated (no `CurrentIdentity` dependency, matching `accept_invitation`'s own pre-login signature), with the path parameter named `invitation_id` — the invitation's real, only identifier — rather than a separate secret `token`, since `invitations` has no dedicated single-use token column today (a real, disclosed schema limitation, not a REST-layer shortcut). `deactivate_access_rule`/`revoke_invitation` deliberately take no `{organization_id}` path parameter, always operating on `actor.organization_id` — the same "no org-override query path" convention this file's pre-existing `/tenancy/connectors` endpoints already established.
- **Depends on:** Files 28, 103, 137–138.
- **Unlocks:** File 146's test coverage (`tests/api/test_tenancy_router.py`'s new admin-surface tests).

### 143. `app/api/routers/auth.py`'s `POST /auth/logout-all` and `app/api/routers/users.py` (new file, integration-gaps addition — read as a revisit of Files 34, 103)
- **Read now because:** "logout everywhere" — a feature `core.auth.service.revoke_all_sessions` (File 34) has been able to perform since Milestone 6/7 with no caller ever reaching it at all.
- **You'll learn:** `POST /auth/logout-all` — self-service, requires `actor.user_id` (a service/agent identity has no sessions of its own, same guard `GET /auth/me` already uses), calls `revoke_all_sessions(session, actor.user_id, actor.organization_id)`, records a `user.logout_all_sessions` audit event, and returns `LogoutAllResponse` (`message`, `revoked_session_count`) — whose own docstring is explicit about a real, easy-to-miss nuance: revoking every `refresh_tokens` row does not invalidate an already-issued, still-unexpired *access* token, since `verify_access_token` (File 34) is a pure, stateless JWT check with no DB lookup at all; "logged out everywhere" means no session can be *refreshed* past its current access token's natural expiry, not an instant global kill switch. The new `app/api/routers/users.py` adds the admin counterpart, `POST /users/{user_id}/logout-all`, gated by `require_permission(actor, "tenancy:manage")` — this codebase's existing stand-in for "organization admin," the same permission every other tenancy-admin operation already requires, not a new admin concept invented for this one feature; no extra tenant-isolation check is needed beyond that permission gate, since `revoke_all_sessions` itself is already scoped to `(user_id, actor.organization_id)`, so a `user_id` belonging to a different organization simply revokes zero rows rather than leaking whether that user exists elsewhere.
- **Depends on:** Files 34, 103.
- **Unlocks:** File 146's test coverage.

### 144. `app/api/routers/knowledge.py`'s `GET`/`PATCH /{document_id}` (integration-gaps addition — read as a revisit of File 103)
- **Read now because:** small and quick, and the direct REST-facing counterpart to File 139's service-layer changes.
- **You'll learn:** both routes are deliberately declared *after* the file's existing `/proposed` and `/gaps` routes — a route-ordering requirement, not a stylistic choice: FastAPI/Starlette matches path operations in registration order, so a `{document_id}` parameter declared first would swallow requests to the literal `/proposed`/`/gaps` paths as if `"proposed"`/`"gaps"` were themselves a document id. `propose_runbook_update` (creating a proposal) remains intentionally unexposed here — File 139's closing note on this boundary applies at the REST layer too.
- **Depends on:** Files 103, 139.
- **Unlocks:** File 146's test coverage (including a dedicated regression test pinning down the route-ordering requirement itself).

### 145. `app/mcp/tools/create_project.py`, `create_invitation.py`, `configure_sso.py`, `create_access_rule.py`, and `app/mcp/servers/main.py`'s registration of them (integration-gaps addition — read as a revisit of Files 100, 102)
- **Read now because:** the MCP-side counterpart to File 142's REST admin surface — four new tools, each following `propose_runbook_update.py`'s (File 100) exact established shape (`extract_bearer_token` → a nested `handler(session, identity)` closure → `run_mcp_tool`) with zero new logic beyond that translation, confirming the dispatch design Stage S built (File 90) still generalizes cleanly to a fourth milestone's worth of new tools with no changes to `dispatch.py` itself.
- **You'll learn:** `create_project`, `create_invitation`, `configure_sso`, `create_access_rule` — each a thin wrapper calling the matching `core.tenancy.service` function (File 28/138) with `identity.organization_id`, registered in `mcp/servers/main.py` alongside the six Milestone 8 tools and the Knowledge Gap Agent's read tools. Consistent with this whole codebase's existing test-suite convention (every prior MCP tool file is untested at the decorated-function level, only `run_mcp_tool`'s dispatch plumbing itself is), none of these four get a dedicated MCP-tool-level unit test — a deliberate consistency, not an oversight, disclosed here rather than left for you to notice as a silent gap.
- **Depends on:** Files 90, 100, 102, 138.
- **Unlocks:** nothing further; closes Stage Z's REST/MCP surface additions.

### 146. `tests/core/users/test_service.py`, `tests/core/tenancy/test_service.py`, `tests/core/knowledge/test_service.py`, `tests/core/incidents/test_service.py`, `tests/api/test_tenancy_router.py`, `tests/api/test_knowledge_router.py`, `tests/api/test_auth_router.py` (new file), `tests/api/test_users_router.py` (new file), `tests/core/auth/test_service.py`, `tests/agents/investigation/test_evidence.py`
- **Read now because:** closes this stage — worth reading as a set, since together they're the concrete answer to this pass's own "user-with-permission succeeds, user-without-permission gets 403, organization isolation works" testing requirement, exercised primarily against `register_connector` (File 138) as the one call site with a fully worked project-scoped-vs-org-scoped branch to test both sides of.
- **You'll learn:** `tests/core/users/test_service.py` gains `test_resolve_identity_populates_project_permissions`/`test_resolve_identity_defaults_to_empty_project_permissions` and three `require_project_permission` unit tests (grant/deny/org-level-fallback) — and its one pre-existing test (`test_resolve_identity_sets_tenant_context_before_querying_roles`, from Stage Y's File 133) is refactored to route through a new shared `_patch_resolve_identity_dependencies` helper patching all four of `resolve_identity`'s dependencies at once, specifically so this pass's new `get_project_permission_map` call didn't silently break it by being the one dependency nobody thought to monkeypatch. `tests/core/tenancy/test_service.py` gains three `register_connector`-with-`project_id` tests: denied when the actor's grant is on a *different* project, succeeds when it's on the matching one, and still denied for an org-wide (`project_id=None`) connector when the actor's only grant is project-scoped — the succeed/deny/isolation triad in concrete form. `tests/api/test_tenancy_router.py` gains full coverage of File 142's thirteen endpoints, including a dedicated test confirming `GET /organizations` calls `get_organization` (not the unscoped `list_organizations`) and one confirming `POST /invitations/{id}/accept` requires no authentication. `tests/api/test_auth_router.py`/`test_users_router.py` (both new) cover self-revocation, admin-revocation, and the 403-for-non-admin path; `tests/core/auth/test_service.py` gains a direct `revoke_all_sessions` unit test (scoping assertion + pass-through of the revoked count) alongside its pre-existing `refresh`/`logout` coverage. `tests/agents/investigation/test_evidence.py` gains `test_monitoring_connector_source_resolves_to_monitoring_live_source` (asserted against the real `_LIVE_SOURCES`, not a fake) and an end-to-end `test_gather_live_evidence_dispatches_monitoring_connector`. `tests/core/incidents/test_service.py` needed no changes at all — its existing, narrow test suite (Stage H, File 39/40) doesn't touch any of the four renamed call sites from File 140.
- **Depends on:** Files 137–145.
- **Unlocks:** nothing further; closes Stage Z.

**A note on deliberate deviations from this pass's original literal specification, disclosed here in the same spirit every other honestly-flagged gap in this roadmap has been:** audit action strings follow this codebase's pre-existing lowercase-dotted convention (`document.update`, `user.logout_all_sessions`, `organization.create`) rather than the `SCREAMING_SNAKE_CASE` names (`KNOWLEDGE_UPDATED`, `USER_LOGOUT_ALL_SESSIONS`) an earlier draft of this pass's own spec used — internal consistency with every one of the 16+ audit actions already in this codebase won out over matching that spec's literal casing. `GET /organizations` returns only the caller's own organization rather than exposing the real, unscoped `list_organizations` — see File 142. The invitation "token" in `POST /invitations/{invitation_id}/accept` is actually the invitation's existing `id`; no separate single-use token column was added. And retrieval-layer permission enforcement was deliberately not added: `app/retrieval/service.py` (File 46) has no `Identity` parameter at all, and its own docstring already states that resolving project access is explicitly not retrieval's job — enforcement instead happens at every call site that has an `Identity` to check in the first place (incidents, knowledge, connector registration, Files 138–140), which is where this pass actually put it.

---

## Stage AA — Decision Log & Project Status (Files 147–148)

Read last, as a retrospective — now that you've seen the code, these two docs explain *why* certain choices were made and what's honestly still outstanding.

### 147. `docs/ENGINEERING_DECISIONS.md`
- **You'll learn:** the numbered decision log you've now seen cited by number throughout the codebase (#001 modular monolith, #002 ingestion as a separate worker, #003 arq over Celery, #004 the org-scoping RBAC fix — closing a real cross-tenant leak in already-committed code, #005 the SSO provisioning-policy model, #006 the embedding model pin, #007 document-level ACL as a single column, #008 OpenAI over Anthropic, #009 the reranker model pin), plus the still-open items (confidence-score formula/threshold tuning, single-vs-multiple MCP servers) — and the Stage W decisions resolved along the way (leader clustering over k-means, the separate `agents/workers` process, the `list_organizations` no-actor exception).
- **Note — this doc is now stale, and unusually explicitly so:** it stops at decision #009 and its "Open" section, dated 2026-08-02 — it was never extended with an entry for any of Stage Y's Milestone 10 decisions (the `LocalKeyManagementService` pre-production trust-boundary tradeoff, in-process vs. Redis-backed rate limiting, the `mcp` 1.x→2.0 SDK port, storing the encryption envelope directly in `connector_configs.credential_ref` rather than a dedicated secrets table) or Stage Z's integration-gaps-pass deviations (the audit-naming-convention choice, `GET /organizations`'s scoping, invitation-id-as-token — see Stage Z's closing note for the full list). Treat every one of those as a real decision this doc simply hasn't caught up to yet, not as evidence the decision was never made.
- **Depends on:** essentially everything before it — every entry will now read as "oh, that's the file where this actually applies."
- **Unlocks:** nothing further; a permanent reference to reread whenever you wonder "why is it built this way" — bearing in mind it is not the complete list, per the note above.

### 148. `docs/PROJECT_STATUS.md`
- **Read last because:** it's a stale "resume work here" snapshot from very early in the project (before most of what you just read existed) — read it purely as a historical artifact of how this codebase's working process operates (one file at a time, tracked via a task list), not as a description of current state. `docs/USER_TESTING_GUIDE.md` (Stage Y, File 136) is the far more current whole-system narrative if you want something trustworthy to hold in your head instead.
- **You'll learn:** the project's working conventions and how far along it was at one specific earlier moment — useful context for *how* this codebase gets built, even though its specific "pending tasks" are now stale.
- **Depends on:** nothing technical.
- **Unlocks:** nothing further — you've completed the roadmap.

---

## What you now understand, end to end

Tracing a single real request through everything you've read: a Slack message or GitHub commit gets fetched by a connector (Stage J, or one of Stage X's six later additions — Jira, Teams, Azure DevOps, Confluence, SharePoint, and the internal `runbooks` connector re-embedding approved postmortems), its credential decrypted just-in-time from an AES-256-GCM envelope and its fetch rate throttled per-connector and per-organization (Stage Y) → cleaned, chunked, embedded, and stored across three pgvector-backed collections, every write and later read scoped by both application-level tenant checks and, since Stage Y, a Postgres Row-Level Security policy enforced at the database itself (Stages C, I, J, Y) → a user (resolved to an `Identity` via `core/users` — now including a fully populated `project_permissions` map, Stage Z — itself provisioned through `core/tenancy`'s SSO rules and `core/auth`'s OIDC flow, Stages D–F) asks a question → the Retrieval Agent hybrid-searches those collections and reranks the results (Stage L) → Confidence Evaluation decides whether there's enough signal to answer directly or investigate (Stage M) → either the Answer Agent generates a grounded, cited response (Stage N), or the Investigation Agent gathers evidence — first from what's already indexed (code, chat, postmortems), then live from GitHub's/Slack's/now also a registered `monitoring` connector's own APIs directly for a genuinely active incident or thin/stale indexed evidence (Stage O's Files 77a-77d, wired to `monitoring` by Stage Z's File 141) — and proposes cited hypotheses, writing its findings back onto the incident's timeline, every project-scoped write now checked via `require_project_permission` rather than a bare org-level permission (Stage O, Stage Z's File 140) → later, once a human resolves that incident, the Postmortem Agent reconstructs the timeline, extracts a root cause, and drafts action items as a `Postmortem` a human must still approve before it becomes "knowledge" (Stage P) → all of this is exposed to external AI clients and human users through a REST API (now including a full tenancy-admin surface — organizations, projects, SSO, access rules, invitations, Stage Z's File 142 — and an observability-dashboard read surface, Stage Y's File 128) and MCP's tools/resources/prompts (Stages S, V, plus Stage Z's four tenancy-admin tools), a human reviewer approving, rejecting, or now editing any AI-proposed runbook update before it's published (Stage V's `core/knowledge`, extended by Stage Z's File 139) → separately, and on its own nightly schedule rather than per-question, the Knowledge Gap Agent scans every organization's recent low-confidence answers, clusters the ones that recur into topics, and surfaces each as a `GapReport` recommendation — never auto-publishing a document itself, always leaving that same human-approval gate in place (Stage W) → a user can revoke every one of their own sessions, or (if an organization admin) someone else's, at any time (Stage Z's File 143).

What's honestly still outstanding, as of this revision: none of Milestone 10's RLS work has ever run against a live Postgres instance (Stage Y's File 135); the rate limiter is in-process, not distributed; `LocalKeyManagementService` doesn't yet deliver a real separate-trust-boundary guarantee; retrieval-layer permission enforcement was deliberately left out of Stage Z's project-permission pipeline, by design, not oversight (see that stage's closing note); no MCP-tool-level unit tests exist for any tool in this codebase, old or new, only `run_mcp_tool`'s dispatch plumbing; and `docs/ENGINEERING_DECISIONS.md`/`docs/PROJECT_STATUS.md` (Stage AA) are now stale relative to Stages Y and Z in addition to the staleness they already had relative to Stage W. None of that is something you missed in your reading — it is the honest, current edge of the project, disclosed in each relevant stage's own closing notes rather than hidden here.
