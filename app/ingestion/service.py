"""Public interface for ingestion/ (PROJECT_PLAN.md section 9.8):
`run_ingestion_job(connector_config_id)`, `reindex(document_id)`,
`get_job_status(job_id)`.

Owned by: ingestion/. Ties together connectors (task #9/#10), the
processing pipeline (task #11), and persistence (this task's repository.py)
into the one job-execution flow. Depends on core/tenancy (reading
`connector_configs` via `ingestion/repository.py`'s direct database read,
and writing sync status back through `core.tenancy.service.update_connector_sync_status`)
and core/users (indirectly, via `Identity.for_agent` -- no permission check
is performed here, see below) -- both undocumented in section 9.8's
dependency list (retrieval/database/shared only), flagged the same way the
connector-config-access gap was flagged and resolved earlier in this
milestone. Also depends on retrieval/ (`retrieval.service.upsert`) to hand
off each processed document's chunks for embedding and storage -- this one
*is* in section 9.8's dependency list.

No `actor: Identity` parameter anywhere in this module's public functions,
matching section 9.8's literal signatures. Ingestion runs as a separate
worker process (section 4.5), triggered by a scheduler/webhook handler, not
synchronously by an end user request -- there is no human caller to
authorize per call the way core/'s user-facing functions require. Every
mutation still gets an actor for audit-tagging purposes: internally, a job
constructs `Identity.for_agent("ingestion_worker", organization_id)`, the
same convenience constructor `core/auth` and `core/incidents` use for
non-interactive callers.

Each processed document's chunks are handed to `retrieval.service.upsert`
inside the same savepoint as the `Document`/`document_metadata` writes, so a
mid-job failure rolls back the chunks along with the row they belong to --
no orphaned embeddings for a document that never successfully persisted.
`_CONTENT_TYPE_TO_COLLECTION` maps `ProcessedDocument.content_type` onto the
`CollectionName` `retrieval/` expects (see that mapping's own comment for
why it lives here rather than in either module it bridges).

Milestone 10 additions (PROJECT_PLAN.md section 12.5/section 10): (1)
`config_row.credential_ref` is decrypted via `app.shared.security` exactly
once per job, immediately before `connector.authenticate()` needs it --
see `_execute_ingestion_job`'s own docstring; (2) every `fetch_batch` call
acquires from two `app.ingestion.rate_limiter.TokenBucketRateLimiter`
budgets first (per-connector_config and per-organization), closing the gap
`app.ingestion.workers.tasks.scheduled_reconciliation`'s docstring used to
flag as "not attempted here" -- except for a connector that declares
`rate_limits_own_requests = True` (2026-08 audit "H2" fix,
`app.ingestion.connectors.base.Connector`'s own docstring), which instead
acquires per real outbound HTTP request itself, from the same shared
limiter (`app.ingestion.rate_limiter.get_ingestion_rate_limiter()`).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.core.tenancy import service as tenancy_service
from app.database.session import set_tenant_context
from app.ingestion import repository
from app.ingestion.connectors.base import Connector
from app.ingestion.connectors.azure_devops import AzureDevOpsConnector
from app.ingestion.connectors.confluence import ConfluenceConnector
from app.ingestion.connectors.github import GitHubConnector
from app.ingestion.connectors.jira import JiraConnector
from app.ingestion.connectors.runbooks import RunbooksConnector
from app.ingestion.connectors.sharepoint import SharePointConnector
from app.ingestion.connectors.slack import SlackConnector
from app.ingestion.connectors.teams import TeamsConnector
from app.ingestion.processors.pipeline import process_document
from app.ingestion.rate_limiter import get_ingestion_rate_limiter
from app.ingestion.schemas import ContentType, IngestionJob, ResolvedConnectorConfig
from app.retrieval import service as retrieval_service
from app.retrieval.schemas import CollectionName, UpsertChunk
from app.shared.config.logging import get_logger
from app.shared.config.settings import get_settings
from app.shared.schemas import Identity
from app.shared.security import decrypt_secret, get_kms

logger = get_logger(__name__)

# One connector instance per known source (PROJECT_PLAN.md section 4.2:
# "source_name must match the `source` value used on connector_configs
# rows"). Connectors are stateless between jobs (see each connector's
# module docstring on why fetch/list state is re-derived per call, not
# cached), so one shared instance per source is safe to reuse across jobs.
_CONNECTOR_REGISTRY: dict[str, Connector] = {
    SlackConnector.source_name: SlackConnector(),
    GitHubConnector.source_name: GitHubConnector(),
    JiraConnector.source_name: JiraConnector(),
    TeamsConnector.source_name: TeamsConnector(),
    AzureDevOpsConnector.source_name: AzureDevOpsConnector(),
    ConfluenceConnector.source_name: ConfluenceConnector(),
    SharePointConnector.source_name: SharePointConnector(),
    RunbooksConnector.source_name: RunbooksConnector(),
}

# How much earlier than `Settings.ingestion_job_timeout_seconds` (which also
# drives arq's own outer `job_timeout`) this module's internal
# `asyncio.wait_for` fires (2026-08 audit "H1" fix). Deliberately nonzero: if
# both timeouts were identical, a race between arq's own outer cancellation
# and this module's internal one could still let arq's raw, uncatchable-by-
# `except Exception` `asyncio.CancelledError` win. This margin exists purely
# to make the internal, catchable path win that race in practice, not
# because the exact number matters.
_TIMEOUT_SAFETY_MARGIN_SECONDS = 30

# `ProcessedDocument.content_type` -> `retrieval.schemas.CollectionName`
# (PROJECT_PLAN.md section 8.2's collection names; see
# `app.database.models.retrieval_models`'s module docstring for the same
# mapping stated from retrieval's side). Lives here, not in ingestion/schemas
# or retrieval/schemas: it names a `CollectionName` (retrieval-owned) keyed
# by a `ContentType` (ingestion-owned), so it belongs to whichever module
# depends on both -- ingestion depends on retrieval (this file), never the
# reverse (import-linter's "retrieval does not depend on ingestion"
# contract), so retrieval/schemas.py cannot hold this mapping itself.
_CONTENT_TYPE_TO_COLLECTION: dict[ContentType, CollectionName] = {
    "document": "documentation",
    "code": "code",
    "chat": "conversations",
}


async def run_ingestion_job(session: AsyncSession, connector_config_id: uuid.UUID) -> IngestionJob:
    """Run one sync for `connector_config_id` -- incremental if it has a
    `last_synced_at`, full otherwise (PROJECT_PLAN.md section 4.4).
    """
    return await _execute_ingestion_job(session, connector_config_id, force_full_sync=False)


async def reindex(session: AsyncSession, document_id: uuid.UUID) -> IngestionJob:
    """Force a fresh sync of the connector_config that produced
    `document_id`, so its content gets re-fetched and re-processed.

    Not a targeted single-document re-fetch: the `Connector` protocol has no
    "fetch this one item by external_id" method, only `fetch_batch` (a full
    or incremental *sync* over many items) -- see `base.Connector`'s
    docstring. "Reindex this document" is implemented here as "run a full
    resync of whatever connector produced it," which does reprocess the
    target document (along with everything else from that source) -- honest
    about the mechanism actually available today rather than a narrower
    operation this milestone's connector protocol can't perform.

    Milestone 10 RLS note: `documents` is RLS-protected, and this function
    starts from a bare `document_id` with no `Identity`/org context yet --
    the same chicken-and-egg shape `_execute_ingestion_job` has for
    `connector_configs`. Resolved the same way: a narrow, RLS-bypassing
    lookup (`repository.resolve_document_organization_id`) discovers just
    the owning organization_id, `set_tenant_context` is set to it, and only
    then does the real, RLS-scoped `get_document_by_id` query run.
    """
    document_organization_id = await repository.resolve_document_organization_id(session, document_id)
    if document_organization_id is None:
        raise NotFoundError(
            "Document not found.",
            error_code="document.not_found",
            detail={"document_id": str(document_id)},
        )
    await set_tenant_context(session, document_organization_id)

    document = await repository.get_document_by_id(session, document_id)
    if document is None:
        raise NotFoundError(
            "Document not found.",
            error_code="document.not_found",
            detail={"document_id": str(document_id)},
        )

    connector_config = await repository.get_connector_config_for_source(
        session, document.organization_id, document.source
    )
    if connector_config is None:
        raise ConflictError(
            "No connector configuration is registered for this document's source; cannot reindex.",
            error_code="document.reindex_unavailable",
            detail={"source": document.source, "organization_id": str(document.organization_id)},
        )

    return await _execute_ingestion_job(session, connector_config.id, force_full_sync=True)


async def get_job_status(session: AsyncSession, job_id: uuid.UUID) -> IngestionJob:
    """Fetch one ingestion job's current status."""
    row = await repository.get_ingestion_job_by_id(session, job_id)
    if row is None:
        raise NotFoundError(
            "Ingestion job not found.",
            error_code="ingestion_job.not_found",
            detail={"job_id": str(job_id)},
        )
    return IngestionJob.model_validate(row)


async def _execute_ingestion_job(
    session: AsyncSession, connector_config_id: uuid.UUID, *, force_full_sync: bool
) -> IngestionJob:
    """Shared implementation behind `run_ingestion_job` and `reindex`.

    Runs to completion within one call (fetches every page for every
    changed/new item, in a loop) rather than being split into
    separately-retriable per-stage arq tasks. PROJECT_PLAN.md section 4.5's
    "a retry resumes from the failed stage, not from scratch" is honored at
    a coarse grain here: `failed_stage` records *which* stage the job died
    in (fetch/normalize/process/persist) for observability, but a retry
    (arq re-invoking this same function) re-runs the whole sync from the
    top, not from that stage's midpoint -- true stage-level resume would
    need each stage to be its own chained, independently-retriable task,
    which is a larger undertaking flagged here rather than silently assumed
    to already exist.

    Milestone 10 addition (PROJECT_PLAN.md section 12.5): `config_row.
    credential_ref` is the envelope-encrypted blob `core.tenancy.service.
    register_connector` stored, not a usable credential -- decrypted here,
    exactly once per job, into the `ResolvedConnectorConfig` handed to
    `connector.authenticate()`. This is the one place in the whole ingestion
    path a plaintext credential exists at all; it is never persisted,
    logged, or held any longer than this function's own local variables
    live.

    Milestone 10 RLS note: this is the one code path in the whole
    application that cannot call `set_tenant_context` before its first
    query, because it starts from a bare `connector_config_id` with no
    `Identity`/org context yet (a worker job argument, not a request that
    already resolved one) -- and `connector_configs` is itself RLS-protected
    by the row this exact call needs to read. Broken via a narrow,
    RLS-bypassing lookup (`repository.resolve_connector_config_organization_id`,
    see `d2e5f8a3c1b6_milestone_10_rls_bypass_functions.py`) that answers
    only "which org owns this connector_config," nothing else; only once
    that's known and `set_tenant_context` is set does the real, RLS-scoped
    `get_connector_config` query below run.

    2026-08 audit "H1" fix -- job lifecycle observability: the `queued` ->
    `running` transition is committed immediately (`session.commit()`),
    before the fetch loop starts, rather than only ever being visible once
    the whole job finishes. Without this, `get_job_status` -- called from a
    completely different request/session/database connection -- could never
    see a job that was still running: Postgres's default READ COMMITTED
    isolation means an uncommitted row is invisible outside the transaction
    that wrote it, and `repository.insert_ingestion_job`/`update_ingestion_
    job`'s `session.flush()` only makes a row visible to *further queries on
    this same session*, never to any other one. This is a deliberate,
    narrow exception to this module's usual "services don't commit their
    own session" convention (see `core.audit.service`'s docstring for why
    that convention exists in the ordinary case) -- required here because
    "is this job actually running yet" must cross a session/transaction
    boundary while the fetch loop is still in progress, which no amount of
    `flush()` can do. Because `set_tenant_context`'s `SET LOCAL` is scoped
    to the transaction it was called in, and the commit above ends that
    transaction, `set_tenant_context` is called again immediately
    afterward, before any further RLS-protected query runs in the new,
    auto-begun transaction.

    2026-08 audit "H1" fix -- timeout/cancellation durability: the fetch
    loop runs inside `asyncio.wait_for(..., timeout=...)`, using a value
    slightly shorter than `Settings.ingestion_job_timeout_seconds` (which
    also drives arq's own outer `job_timeout`, `app.ingestion.workers.main.
    WorkerSettings`). This converts the common case of "this job ran too
    long" from an external `asyncio.CancelledError` raised by arq's outer
    cancellation -- a `BaseException`, not caught by this function's own
    `except Exception`, nor by `run_ingestion_job_task`'s, nor by
    `session_scope`'s, so it used to unwind straight past every one of
    them and erase the *entire* transaction, including the job row itself
    -- into an ordinary `asyncio.TimeoutError`, a normal `Exception`
    subclass this function already knows how to record as `status=
    "failed"`. A genuine external cancellation (arq's own outer timeout
    still firing despite the margin above, or a worker shutdown signal) is
    handled by a dedicated `except asyncio.CancelledError` clause: unlike
    the ordinary-failure branch, it commits the failure record itself
    before re-raising, since a `CancelledError` must still propagate
    (asyncio's own contract), which would otherwise reach `session_scope`'s
    `finally: session.close()` and discard an uncommitted write.
    """
    organization_id = await repository.resolve_connector_config_organization_id(
        session, connector_config_id
    )
    if organization_id is None:
        raise NotFoundError(
            "Connector configuration not found.",
            error_code="connector_config.not_found",
            detail={"connector_config_id": str(connector_config_id)},
        )
    await set_tenant_context(session, organization_id)

    config_row = await repository.get_connector_config(session, connector_config_id)
    if config_row is None:
        raise NotFoundError(
            "Connector configuration not found.",
            error_code="connector_config.not_found",
            detail={"connector_config_id": str(connector_config_id)},
        )

    connector = _CONNECTOR_REGISTRY.get(config_row.source)
    if connector is None:
        raise ConflictError(
            f"No connector implementation is registered for source '{config_row.source}'.",
            error_code="ingestion.unsupported_source",
            detail={"source": config_row.source},
        )

    actor = Identity.for_agent("ingestion_worker", config_row.organization_id)
    plaintext_credential = decrypt_secret(get_kms(), config_row.credential_ref)
    resolved_config = ResolvedConnectorConfig(
        connector_config_id=config_row.id,
        organization_id=config_row.organization_id,
        project_id=config_row.project_id,
        source=config_row.source,
        credential_ref=plaintext_credential,
        config=config_row.config,
    )

    job_row = await repository.insert_ingestion_job(
        session, organization_id=config_row.organization_id, connector_config_id=connector_config_id
    )
    job_row = await repository.update_ingestion_job(
        session, job_row.id, status="running", started_at=datetime.now(timezone.utc)
    )
    if job_row is None:
        raise RuntimeError("Ingestion job disappeared mid-update.")  # unreachable: just inserted above

    # H1 fix: commit the running transition now, in its own short
    # transaction, so a concurrent `get_job_status` call (a different
    # session/connection) can see it immediately rather than only once this
    # entire sync finishes -- see this function's own docstring for the
    # full "services don't normally commit" caveat this deliberately,
    # narrowly overrides.
    await session.commit()
    # The commit above ended the transaction `set_tenant_context` set the
    # GUC in (`SET LOCAL` is transaction-scoped) -- re-set it for the new,
    # auto-begun transaction before any further RLS-protected query runs.
    await set_tenant_context(session, organization_id)

    since = None if force_full_sync else config_row.last_synced_at
    documents_processed = 0
    stage = "authenticate"
    client = None
    # Fixed for the whole sync (not threaded through `cursor`, which is
    # purely intra-sync pagination) -- see `FetchResult.resume_token`'s own
    # docstring on why a connector re-decodes this same value on every call
    # rather than the caller updating it mid-sync.
    connector_supports_resume_token = getattr(connector, "supports_resume_token", False)
    resume_token_in = config_row.config.get("_resume_token") if connector_supports_resume_token else None
    latest_resume_token: str | None = None
    # 2026-08 audit "H2" fix -- see `Connector.rate_limits_own_requests`'s
    # own docstring (`app.ingestion.connectors.base`).
    connector_rate_limits_own_requests = getattr(connector, "rate_limits_own_requests", False)

    async def _run_sync() -> None:
        # The fetch/normalize/process/persist loop runs inside a savepoint
        # (a nested transaction), not the outer transaction directly. This
        # matters because `job_row`'s "running" transition was committed
        # separately above, which nothing here ever commits again (services
        # don't commit their own session -- see core.audit.service's
        # docstring on why -- except for the one deliberate exception just
        # above). Without the savepoint, a mid-loop failure would have no
        # clean way to roll back just the failed attempt's writes: rolling
        # back the *whole* transaction would also erase this attempt's
        # ability to record why it failed. `begin_nested()` rolls back only
        # its own block on exception, leaving `job_row` (and the session
        # generally) intact and usable in the `except` clauses below.
        nonlocal client, documents_processed, latest_resume_token, stage
        async with session.begin_nested():
            client = await connector.authenticate(resolved_config)
            cursor: str | None = None
            while True:
                stage = "fetch"
                # Two independent budgets, both acquired before every fetch
                # (PROJECT_PLAN.md sections 4.5/10: "per connector, per
                # tenant") -- see `app.ingestion.rate_limiter`'s module
                # docstring for why both are needed and what each is for.
                #
                # Skipped for a connector that acquires its own tokens per
                # real outbound HTTP request instead (2026-08 audit "H2"
                # fix, `Connector.rate_limits_own_requests` -- `JiraConnector`
                # is the one example so far): acquiring here too would be a
                # redundant, extra token consumed on top of what that
                # connector already acquires internally for the exact same
                # shared buckets.
                if not connector_rate_limits_own_requests:
                    await get_ingestion_rate_limiter().acquire(
                        f"connector:{connector_config_id}", connector.requests_per_second
                    )
                    await get_ingestion_rate_limiter().acquire(
                        f"org:{config_row.organization_id}",
                        get_settings().ingestion_org_max_requests_per_second,
                    )
                fetch_kwargs: dict[str, Any] = {"since": since, "cursor": cursor}
                if connector_supports_resume_token:
                    fetch_kwargs["resume_token"] = resume_token_in
                fetch_result = await connector.fetch_batch(client, **fetch_kwargs)
                if fetch_result.resume_token is not None:
                    latest_resume_token = fetch_result.resume_token

                stage = "process_item"
                for raw_item in fetch_result.items:
                    documents_processed += await _process_one_item(
                        session,
                        connector=connector,
                        raw_item=raw_item,
                        resolved_config=resolved_config,
                        actor=actor,
                    )

                if not fetch_result.has_more:
                    break
                cursor = fetch_result.next_cursor

    try:
        # H1 fix: bounded by a timeout slightly shorter than arq's own
        # outer `job_timeout` (see this function's docstring and
        # `_TIMEOUT_SAFETY_MARGIN_SECONDS`'s own comment) -- converts the
        # common "this job ran too long" case into an ordinary, catchable
        # `asyncio.TimeoutError` (handled by the `except Exception` clause
        # below, via `_is_timeout`) instead of relying on arq's outer,
        # uncatchable-by-`except Exception` `asyncio.CancelledError`.
        fetch_timeout_seconds = max(
            get_settings().ingestion_job_timeout_seconds - _TIMEOUT_SAFETY_MARGIN_SECONDS, 1
        )
        await asyncio.wait_for(_run_sync(), timeout=fetch_timeout_seconds)

        completed_at = datetime.now(timezone.utc)
        job_row = await repository.update_ingestion_job(
            session,
            job_row.id,
            status="succeeded",
            documents_processed=documents_processed,
            completed_at=completed_at,
        )
        await tenancy_service.update_connector_sync_status(
            session,
            actor,
            config_row.organization_id,
            connector_config_id,
            status="active",
            last_synced_at=completed_at,
            config_patch={"_resume_token": latest_resume_token}
            if latest_resume_token is not None
            else None,
        )
    except asyncio.CancelledError:
        # H1 fix: a genuine *external* cancellation (arq's own outer
        # `job_timeout` still firing despite `_TIMEOUT_SAFETY_MARGIN_
        # SECONDS`, or a worker shutdown signal) rather than this
        # function's own internal `asyncio.wait_for` timeout (that raises
        # `asyncio.TimeoutError`, an ordinary `Exception` handled below,
        # not this). Unlike the `except Exception` branch, this commits the
        # failure record itself, right here, before re-raising --
        # `CancelledError` must still propagate (asyncio's own contract: a
        # task that swallows cancellation without re-raising leaves the
        # caller unable to tell the difference between "finished" and "was
        # cancelled"), and letting it reach `session_scope`'s `finally:
        # session.close()` uncommitted would discard this exact write, the
        # same "zero trace" failure mode this whole fix exists to close.
        logger.warning(
            "ingestion_job_cancelled",
            job_id=str(job_row.id),
            connector_config_id=str(connector_config_id),
            stage=stage,
        )
        job_row = await repository.update_ingestion_job(
            session,
            job_row.id,
            status="failed",
            failed_stage=f"{stage}:cancelled",
            completed_at=datetime.now(timezone.utc),
        )
        await tenancy_service.update_connector_sync_status(
            session,
            actor,
            config_row.organization_id,
            connector_config_id,
            status="error",
            config_patch={"_resume_token": latest_resume_token}
            if latest_resume_token is not None
            else None,
        )
        await session.commit()
        raise
    except Exception as exc:
        # `asyncio.TimeoutError` (raised by this function's own internal
        # `asyncio.wait_for` above) is an ordinary `Exception` subclass, so
        # it lands here rather than in the `CancelledError` clause above --
        # `_is_timeout` just labels it distinctly (`failed_stage` gets a
        # ":timeout" suffix) so it's not indistinguishable from any other
        # mid-fetch failure.
        _is_timeout = isinstance(exc, asyncio.TimeoutError)
        logger.warning(
            "ingestion_job_failed",
            job_id=str(job_row.id),
            connector_config_id=str(connector_config_id),
            stage=stage,
            error=str(exc),
            timed_out=_is_timeout,
        )
        # `documents_processed` deliberately NOT reported here: the savepoint
        # above rolled back every document/metadata write this attempt made,
        # so the in-memory counter no longer matches what's actually
        # persisted -- reporting it would claim documents were processed
        # that the rollback just erased. The job row keeps whatever count it
        # already had (0, from `insert_ingestion_job`'s default).
        #
        # Deliberately NOT re-raised: this whole call runs inside the one
        # session/transaction the caller's `session_scope()` opened, and
        # that helper's contract is "commit on normal return, rollback on
        # any exception that escapes." Re-raising here used to undo this
        # very `status="failed"` write (and the original `insert_ingestion_
        # job` row with it) the instant it reached that boundary -- every
        # failed ingestion job left zero trace in `ingestion_jobs`, despite
        # this except block's own intent. Returning normally instead lets
        # the failure record actually commit; `run_ingestion_job_task` (the
        # caller that decides on arq retries) checks `job.status` for this
        # reason rather than relying on a caught exception.
        job_row = await repository.update_ingestion_job(
            session,
            job_row.id,
            status="failed",
            failed_stage=f"{stage}:timeout" if _is_timeout else stage,
            completed_at=datetime.now(timezone.utc),
        )
        await tenancy_service.update_connector_sync_status(
            session,
            actor,
            config_row.organization_id,
            connector_config_id,
            status="error",
            # Safe even on failure: a site/channel whose walk didn't reach
            # completion this run simply keeps whatever token it already
            # had (never overwritten), which is still valid to resume from
            # next time -- only sites that *did* complete get a fresher one.
            config_patch={"_resume_token": latest_resume_token}
            if latest_resume_token is not None
            else None,
        )
    finally:
        if client is not None:
            await connector.close(client)

    if job_row is None:
        raise RuntimeError("Ingestion job disappeared mid-update.")  # unreachable: updated above
    return IngestionJob.model_validate(job_row)


async def _process_one_item(
    session: AsyncSession,
    *,
    connector: Connector,
    raw_item: object,
    resolved_config: ResolvedConnectorConfig,
    actor: Identity,
) -> int:
    """Normalize, process, and persist one raw item.

    Returns 1 if it produced a new document version, 0 if the fetched
    content was unchanged (idempotent no-op, per DATABASE_DESIGN.md's
    `(organization_id, source, external_id, content_hash)` key) -- the
    caller sums these into `documents_processed`. Any exception here
    propagates to `_execute_ingestion_job`'s caller with the coarse
    `"process_item"` stage already recorded -- see that function's
    docstring on why stage-level granularity stops there.
    """
    raw_document = connector.normalize(raw_item)
    processed = process_document(raw_document)

    existing = await repository.get_latest_document(
        session, resolved_config.organization_id, processed.source, processed.external_id
    )
    if existing is not None and existing.content_hash == processed.content_hash:
        return 0  # unchanged -- idempotent no-op

    if resolved_config.project_id is not None:
        project_id = resolved_config.project_id
    else:
        # An org-wide connector_config has no project_id of its own; fall
        # back to the organization's default project -- the same policy
        # core.incidents.service.create_incident uses when IncidentCreate
        # omits project_id.
        default_project = await tenancy_service.get_default_project(
            session, actor, resolved_config.organization_id
        )
        project_id = default_project.id

    next_version = (existing.version + 1) if existing is not None else 1
    document_row = await repository.insert_document(
        session,
        organization_id=resolved_config.organization_id,
        project_id=project_id,
        source=processed.source,
        external_id=processed.external_id,
        content_hash=processed.content_hash,
        title=processed.title,
        source_url=processed.source_url,
        version=next_version,
    )
    await repository.insert_document_metadata(
        session, document_id=document_row.id, entries=processed.metadata_entries
    )

    # processed.chunks are never persisted by ingestion itself --
    # `<collection>_chunks` tables are retrieval-owned (Chunk's docstring in
    # ingestion/schemas.py). Handed off here, inside the same savepoint as
    # the Document/document_metadata writes above, so a mid-job failure
    # rolls back the chunks along with the row they belong to.
    collection = _CONTENT_TYPE_TO_COLLECTION[processed.content_type]
    upsert_chunks = [
        UpsertChunk(
            document_id=document_row.id,
            organization_id=resolved_config.organization_id,
            project_id=project_id,
            collection=collection,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            source_offset_start=chunk.source_offset_start,
            source_offset_end=chunk.source_offset_end,
            acl_permission_code=document_row.acl_permission_code,
        )
        for chunk in processed.chunks
    ]
    await retrieval_service.upsert(session, upsert_chunks)
    return 1
