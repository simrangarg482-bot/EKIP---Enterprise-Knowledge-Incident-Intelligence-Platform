# EKIP — Database Design

Status: **Living document. Update whenever schema changes — including migrations.** This is the detailed companion to `ARCHITECTURE.md` §8; that section stays a summary, this file is the source of truth for actual columns, types, and indexes.

Last updated: 2026-07-20

Target: **Neon Serverless PostgreSQL**, with the `pgvector` extension enabled for collections that use pgvector as their backend (per the per-collection choice explained in `ARCHITECTURE.md` §8).

---

## Conventions

- Every table has `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT now()` unless noted otherwise.
- Soft deletes are used for anything that participates in an audit trail (`documents`, `incidents`, `postmortems`) via a nullable `deleted_at`; nothing that feeds the audit log is ever hard-deleted.
- Foreign keys are `ON DELETE RESTRICT` by default — explicit `CASCADE` is called out where used, so accidental data loss requires a deliberate choice, not a default.
- Table ownership follows the module boundaries in `ARCHITECTURE.md` §3: each table is listed under the module that owns writes to it. Other modules may read through that module's public interface, never write directly.

---

## `core/` — owned tables

### `users`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| email | TEXT UNIQUE NOT NULL | |
| display_name | TEXT NOT NULL | |
| is_active | BOOLEAN NOT NULL DEFAULT true | |
| created_at / updated_at | TIMESTAMPTZ | |

Index: unique on `email` (already implied by constraint, listed for clarity).

### `roles`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| name | TEXT UNIQUE NOT NULL | e.g. `engineer`, `incident_commander`, `admin` |
| description | TEXT | |

### `permissions`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| code | TEXT UNIQUE NOT NULL | e.g. `incident:write`, `postmortem:approve`, `knowledge:publish` |
| description | TEXT | |

### `role_permissions` (join table)
| column | type | notes |
|---|---|---|
| role_id | UUID FK → roles.id | ON DELETE CASCADE |
| permission_id | UUID FK → permissions.id | ON DELETE CASCADE |

Primary key: composite `(role_id, permission_id)`.

### `user_roles` (join table)
| column | type | notes |
|---|---|---|
| user_id | UUID FK → users.id | ON DELETE CASCADE |
| role_id | UUID FK → roles.id | ON DELETE CASCADE |

Primary key: composite `(user_id, role_id)`.

*Why roles/permissions are separated rather than a single enum on `users`:* RBAC needs to support MCP-scoped access checks (`ARCHITECTURE.md` §6) using the same permission codes as the REST API — a flat role enum would force duplicating permission logic per entry point.

### `incidents`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| title | TEXT NOT NULL | |
| description | TEXT NOT NULL | |
| status | TEXT NOT NULL | `open` / `investigating` / `resolved` / `closed` |
| severity | TEXT NOT NULL | |
| owner_team | TEXT | nullable — may be unknown until triage assigns it |
| reported_by | UUID FK → users.id | ON DELETE RESTRICT |
| resolved_at | TIMESTAMPTZ | nullable |
| deleted_at | TIMESTAMPTZ | nullable — soft delete |
| created_at / updated_at | TIMESTAMPTZ | |

Indexes: `status`, `severity`, `created_at DESC` (for recency-sorted incident lists), and a GIN trigram index on `title`/`description` if lexical incident search is needed independent of the vector store.

### `incident_timeline`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| incident_id | UUID FK → incidents.id | ON DELETE CASCADE |
| event_type | TEXT NOT NULL | e.g. `status_change`, `note`, `evidence_added` |
| event_data | JSONB NOT NULL | structured payload, shape depends on `event_type` |
| actor | TEXT NOT NULL | `user:<id>` or `agent:<agent_name>` — distinguishes human vs. AI-authored timeline entries |
| occurred_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Index: `(incident_id, occurred_at)` — timeline is always read in order for one incident.

*Why `actor` is a tagged string rather than a strict FK:* timeline entries can be authored by agents that have no `users` row. Keeping this as an explicit tagged field (rather than a nullable FK plus a separate boolean) makes "who/what did this" unambiguous at the query level, which matters for the human-vs-AI distinction required in `ARCHITECTURE.md` §5.

### `postmortems`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| incident_id | UUID FK → incidents.id | ON DELETE RESTRICT |
| status | TEXT NOT NULL | `draft` / `in_review` / `approved` / `published` |
| root_cause | TEXT | nullable until drafted |
| action_items | JSONB | list of `{description, owner, status}` |
| generated_by | TEXT NOT NULL | `agent:postmortem_agent` or `user:<id>` — same human/AI distinction as timeline |
| reviewed_by | UUID FK → users.id | nullable — set on approval |
| deleted_at | TIMESTAMPTZ | nullable |
| created_at / updated_at | TIMESTAMPTZ | |

Index: `(incident_id)`, `status`.

*Why `status` has both `draft` and `approved`/`published` states rather than a boolean `is_approved`:* mirrors the mandatory human-review gate from `ARCHITECTURE.md` §5 — a postmortem must never silently become "knowledge" without an explicit state transition that's auditable.

### `audit_logs`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| actor | TEXT NOT NULL | same tagged-string convention as timeline |
| action | TEXT NOT NULL | e.g. `incident.create`, `postmortem.approve`, `knowledge.publish` |
| resource_type | TEXT NOT NULL | |
| resource_id | UUID | |
| metadata | JSONB | |
| occurred_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Index: `(resource_type, resource_id)`, `occurred_at DESC`. Append-only — no updates, no deletes, ever.

---

## `agents/` — owned tables

### `agent_executions`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| agent_name | TEXT NOT NULL | e.g. `retrieval_agent`, `investigation_agent` |
| trigger_source | TEXT NOT NULL | `mcp` / `core_api` / `scheduled` |
| input_summary | JSONB | not the full prompt — a structured summary for observability, to avoid storing sensitive full context indefinitely |
| confidence_score | NUMERIC | nullable — set by Confidence Evaluation node where applicable |
| status | TEXT NOT NULL | `running` / `succeeded` / `failed` |
| error_detail | TEXT | nullable |
| started_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |
| completed_at | TIMESTAMPTZ | nullable |

Index: `(agent_name, started_at DESC)` — used by the Knowledge Gap Agent to analyze recent low-confidence patterns.

*Why this table matters beyond logging:* it's the data source for the Knowledge Gap Agent (`ARCHITECTURE.md` §7) — repeated low-`confidence_score` executions on similar `input_summary` topics are the signal that drives documentation-gap recommendations.

---

## `mcp/` — owned tables

### `mcp_requests`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| tool_name | TEXT NOT NULL | |
| identity | TEXT NOT NULL | resolved user/service identity, per `ARCHITECTURE.md` §6 |
| request_summary | JSONB | |
| status_code | INTEGER | |
| latency_ms | INTEGER | |
| occurred_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Index: `(tool_name, occurred_at DESC)` — feeds the MCP latency metrics called out in the original spec's observability section.

---

## `ingestion/` — owned tables

### `ingestion_jobs`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| source | TEXT NOT NULL | `slack` / `github` / `jira` / `docs` |
| source_config | JSONB NOT NULL | connector-specific config (channel, repo, etc.) |
| status | TEXT NOT NULL | `queued` / `running` / `succeeded` / `failed` |
| failed_stage | TEXT | nullable — which pipeline stage failed, per the idempotent-resume design in `ARCHITECTURE.md` §5 |
| documents_processed | INTEGER NOT NULL DEFAULT 0 | |
| started_at | TIMESTAMPTZ | nullable |
| completed_at | TIMESTAMPTZ | nullable |
| created_at | TIMESTAMPTZ NOT NULL DEFAULT now() | |

Index: `status`, `(source, created_at DESC)`.

### `documents`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| source | TEXT NOT NULL | |
| external_id | TEXT NOT NULL | source-native ID (e.g. Slack message ts, GitHub file path) |
| content_hash | TEXT NOT NULL | used for the idempotency key described in `ARCHITECTURE.md` §5 |
| title | TEXT | |
| source_url | TEXT | |
| status | TEXT NOT NULL | `proposed` / `published` — the human-review gate distinction from `ARCHITECTURE.md` §5 |
| version | INTEGER NOT NULL DEFAULT 1 | incremented on content change, not overwritten |
| deleted_at | TIMESTAMPTZ | nullable |
| created_at / updated_at | TIMESTAMPTZ | |

Unique constraint: `(source, external_id, content_hash)` — this *is* the idempotency mechanism; a re-ingest with an unchanged hash conflicts harmlessly (upsert no-op) instead of duplicating.
Index: `status` (agent-proposed documentation is filtered out of retrieval until `published`, unless explicitly querying the review queue).

### `document_metadata`
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| document_id | UUID FK → documents.id | ON DELETE CASCADE |
| key | TEXT NOT NULL | e.g. `author`, `team`, `repo` |
| value | TEXT NOT NULL | |

Index: `(document_id)`, and `(key, value)` for metadata-filtered retrieval.

*Why EAV-style metadata instead of fixed columns:* different sources produce genuinely different metadata shapes (a Slack message has a channel and thread; a GitHub file has a repo and path) — a fixed-column table would need frequent migrations as new connectors are added, which cuts against the "connectors are pluggable" goal in `ARCHITECTURE.md` §3.

---

## `retrieval/` — owned tables (pgvector-backed collections only)

Chunk-level tables exist per collection, following the same shape; documented once here and reused:

### `<collection>_chunks` (e.g. `documentation_chunks`, `code_chunks`)
| column | type | notes |
|---|---|---|
| id | UUID PK | |
| document_id | UUID FK → documents.id | ON DELETE CASCADE |
| chunk_index | INTEGER NOT NULL | order within the source document |
| content | TEXT NOT NULL | |
| embedding | VECTOR(N) | `N` = embedding model's dimension; pgvector column, only present for collections using the pgvector backend |
| source_offset_start | INTEGER | preserves source-anchored offsets for citation, per `ARCHITECTURE.md` §5 |
| source_offset_end | INTEGER | |

Index: HNSW or IVFFlat index on `embedding` (algorithm choice deferred — logged as an open item below), plus `(document_id, chunk_index)`.

For collections using **Qdrant** instead (per the per-collection choice in `ARCHITECTURE.md` §8), the equivalent chunk record lives in Qdrant's payload, not in Postgres — only `document_id`/`chunk_index` bookkeeping needed for citation lookups stays in Postgres, joined back to Qdrant by a shared chunk ID.

---

## Relationships summary

```
users ──< user_roles >── roles ──< role_permissions >── permissions
users ──< incidents (reported_by)
incidents ──< incident_timeline
incidents ──< postmortems
documents ──< document_metadata
documents ──< <collection>_chunks
```

## Open items (to resolve when we reach the `database/` implementation phase)

- pgvector index type (HNSW vs. IVFFlat) and its tuning parameters — deferred until real data volume is known.
- Embedding dimension `N` — depends on final embedding model choice (not yet pinned in `ENGINEERING_DECISIONS.md`).
- Whether `agent_executions.input_summary` needs a retention/TTL policy for privacy/storage reasons.

These will get their own `ENGINEERING_DECISIONS.md` entries once decided.