"""The connector protocol every ingestion source implements.

Owned by: ingestion/connectors/. Per PROJECT_PLAN.md sections 4.1-4.2: a
connector's only job is authenticate + fetch + normalize -- it must not
decide what's worth chunking, must not dedupe, must not embed, and must not
know anything about incidents, postmortems, or confidence scoring. Modeled
as a `typing.Protocol`, not a base class, deliberately: composition over
inheritance, so a connector can't accidentally inherit pipeline behavior it
shouldn't own (the exact rationale given in section 4.2).

`authenticate`/`fetch_batch` are async (both do real network I/O against the
external source); `normalize` is sync (pure data transformation, no I/O) --
matching the async-throughout convention already used across this codebase
(core/auth's httpx-based OIDC calls, the async SQLAlchemy session).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.ingestion.schemas import FetchResult, RawDocument, ResolvedConnectorConfig

#: Each connector's `authenticate` returns whatever real client object that
#: source's SDK/HTTP client needs (a Slack `WebClient`, a plain
#: `httpx.AsyncClient` configured for GitHub's REST API, ...). There is no
#: meaningful common shape across sources beyond "the connector itself knows
#: what to do with it" -- `fetch_batch` only ever receives it back from the
#: same connector's own `authenticate`, never a different connector's client.
AuthenticatedClient = Any


@runtime_checkable
class Connector(Protocol):
    """One connector per external source (Slack, GitHub, ...).

    `source_name` must match the `source` value used on `connector_configs`
    rows and `documents.source` (e.g. `"slack"`, `"github"`) -- this is how
    the worker (task #12) selects the right connector implementation for a
    given `connector_config`.
    """

    source_name: str
    #: Requests/second this source's API tolerates (a conservative constant
    #: is fine; sources with a token-bucket budget instead of a flat rate
    #: should declare their steady-state refill rate here). PROJECT_PLAN.md
    #: section 4.5: the worker pool enforces this *per connector_config*, not
    #: globally, so one tenant's aggressive sync can't starve another
    #: tenant's -- the connector only declares the ceiling, it does not
    #: enforce it itself.
    requests_per_second: float
    #: Opt-in: whether this connector accepts/returns a `FetchResult.
    #: resume_token` that `ingestion.service._execute_ingestion_job`
    #: persists across separate sync runs (not just across pages of one
    #: sync, the way `cursor` already does). Defaults to `False` so every
    #: connector written before this flag existed needs zero change --
    #: the caller only ever passes `resume_token=` to `fetch_batch` when
    #: this is `True`. `SharePointConnector`/`TeamsConnector` set it `True`
    #: to resume their Graph delta walks from where the last sync left off
    #: instead of re-walking from scratch every time.
    supports_resume_token: bool = False
    #: Opt-out (2026-08 audit "H2" fix): whether this connector acquires its
    #: own rate-limiter tokens per real outbound HTTP request, rather than
    #: relying on `ingestion.service._execute_ingestion_job`'s generic
    #: acquisition of one token per `fetch_batch()` call. Defaults to
    #: `False` so every connector written before this flag existed is
    #: unaffected. A connector whose `fetch_batch` can make many real HTTP
    #: requests per call (`JiraConnector`'s search-then-per-issue-
    #: description-and-comment shape is the one example so far) sets this
    #: `True` and calls `app.ingestion.rate_limiter.
    #: get_ingestion_rate_limiter().acquire(...)` itself, once per real
    #: request, using the same `"connector:{connector_config_id}"`/
    #: `"org:{organization_id}"` key shapes the generic path uses -- see
    #: `app.ingestion.rate_limiter`'s module docstring for why this is a
    #: per-connector opt-out rather than instrumenting every connector.
    rate_limits_own_requests: bool = False

    async def authenticate(self, config: ResolvedConnectorConfig) -> AuthenticatedClient:
        """Build an authenticated client for this source from `config`.

        `config.credential_ref` is a reference into the secrets store, not
        yet a resolved secret -- see `ResolvedConnectorConfig`'s docstring.
        Until `shared/security` exists, connector implementations treat it as
        the literal credential value, flagged as a placeholder at each call
        site (matching `core.auth.service._resolve_client_secret`'s existing
        precedent for this exact gap).
        """
        ...

    async def fetch_batch(
        self,
        client: AuthenticatedClient,
        *,
        since: datetime | None,
        cursor: str | None,
    ) -> FetchResult:
        """Fetch one page of raw items.

        `since=None` and `cursor=None` together mean "full sync from the
        beginning"; `since=last_successful_sync_at` with `cursor=None` means
        "incremental sync, from the top"; a non-None `cursor` resumes a
        specific in-progress page sequence (PROJECT_PLAN.md sections 4.2/4.4).

        A connector with `supports_resume_token = True` additionally accepts
        a keyword-only `resume_token: str | None = None` param (the caller
        only ever passes it when that flag is set, so every other
        connector's signature is unaffected) and may set `FetchResult.
        resume_token` on its return value -- see that field's own docstring
        in `app.ingestion.schemas.FetchResult`.
        """
        ...

    def normalize(self, raw_item: Any) -> RawDocument:
        """Convert one source-native item into the common `RawDocument`
        shape. This is the connector's only interpretive step -- everything
        after this (cleaning, chunking, embedding) is the shared processing
        pipeline's job (task #11), not the connector's (section 4.1).
        """
        ...

    async def close(self, client: AuthenticatedClient) -> None:
        """Release whatever resources `authenticate` opened (an HTTP
        connection pool, ...).

        Added alongside the worker (task #12) rather than at this protocol's
        original definition: `AuthenticatedClient` is deliberately `Any` --
        there is no common shape a generic caller could duck-type a cleanup
        call against, so each connector must know how to close its own
        client. The worker calls this in a `finally` block around every job,
        successful or not.
        """
        ...
