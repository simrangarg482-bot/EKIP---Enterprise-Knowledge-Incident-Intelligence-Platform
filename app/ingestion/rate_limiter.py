"""Rate limiting for ingestion jobs (PROJECT_PLAN.md sections 4.5/10:
"Rate limiting per connector, per tenant... the worker pool enforces this
per-`connector_config`... critically, per organization's connection, not
globally").

Every connector already *declares* a `requests_per_second` ceiling (see
`app.ingestion.connectors.base.Connector`'s own docstring), but until this
module existed, nothing actually enforced it -- `app.ingestion.workers.
tasks.scheduled_reconciliation`'s own docstring flagged this explicitly
("each enqueued job is independently rate-limited per connector_config...
not attempted here"). This module closes that gap.

Two independent buckets per job, both acquired before every `fetch_batch`
call in `ingestion.service._execute_ingestion_job`'s fetch loop:
  1. A **per-connector_config** bucket, at that connector's own declared
     `requests_per_second` -- keeps one connector's sync within the ceiling
     it declared for itself.
  2. A **per-organization** bucket, at a fixed aggregate cap
     (`settings.ingestion_org_max_requests_per_second`) -- keeps one
     organization's *combined* connectors (e.g. Jira + Confluence + GitHub
     all syncing at once) from collectively exceeding a shared budget, even
     if each individually stays under its own ceiling. This is the "per
     tenant" half of the requirement a purely per-connector limiter would
     miss entirely.

Known, disclosed limitation: this is an **in-process** token bucket (a
module-level dict, not Redis-backed) -- correct for a single worker process,
but multiple concurrent worker *processes* (arq supports running more than
one) would each enforce their own, independent view of the same budget,
effectively multiplying the real ceiling by the process count. A
Redis-backed distributed token bucket (using the same Redis instance the
job queue already depends on, ENGINEERING_DECISIONS.md #003) is the correct
production fix and is flagged here as follow-up work, not silently assumed
solved.

Also disclosed: one `fetch_batch` call can itself perform more than one real
outbound HTTP request internally (e.g. `GitHubConnector`'s per-file content
fetch, `AzureDevOpsConnector`'s WIQL-then-batch-fetch pair) -- acquiring one
token per `fetch_batch` call is therefore an approximation of "requests per
second," not an exact per-HTTP-call throttle. Building a precise per-call
throttle would mean instrumenting six connectors' internal `httpx` calls
individually, a meaningfully larger change than this pass's scope.

2026-08 audit "H2" fix: `JiraConnector` is the one connector where this
approximation stopped being a reasonable one -- its `fetch_batch` can make
up to ~101 real HTTP requests (1 search + up to 50 description fetches + up
to 50 comment fetches) per single per-`fetch_batch` token, meaning the
configured 2.0 req/s budget was not actually being respected for real
outbound traffic. Rather than instrumenting every connector (still out of
scope, per the paragraph above), `JiraConnector` opts out of the generic
per-`fetch_batch` acquisition (`Connector.rate_limits_own_requests = True`,
`app.ingestion.connectors.base.Connector`'s own docstring) and instead
acquires a token before each of its three real HTTP calls itself, using the
*same* shared limiter instance this module exposes via
`get_ingestion_rate_limiter()` -- not a second, independent bucket, which
would double-count against a different budget than the rest of the
application shares. Every other connector's behavior (and configured rate)
is unchanged.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    """A simple, dependency-free async token bucket, keyed by an arbitrary
    string so one instance can back many independent budgets (one per
    connector_config, one per organization) without a separate object per
    key having to be constructed and threaded through by callers.

    Burst capacity equals `rate` itself (one second's worth of budget), with
    a floor of 1.0: consuming one token per `acquire()` call means a bucket
    that could never hold at least 1.0 tokens could never grant one, no
    matter how long it accrued -- an infinite loop, not a throttle, for any
    `rate < 1.0` token/second (in this codebase, only `SlackConnector.
    requests_per_second = 0.5`). The 1.0 floor keeps the *steady-state*
    rate exactly `rate` (unchanged) while making the burst allowance for a
    sub-1/s caller "wait long enough to earn one token" instead of "never".
    """

    def __init__(self) -> None:
        # key -> (available_tokens, last_refill_monotonic_time)
        self._buckets: dict[str, tuple[float, float]] = {}
        # Guards read-modify-write of `_buckets` for a given key across
        # concurrent `acquire` calls within this process (e.g. two jobs for
        # the same organization running concurrently) -- without this, two
        # coroutines could both read a stale token count and both proceed
        # immediately, defeating the limiter.
        self._lock = asyncio.Lock()

    async def acquire(self, key: str, rate: float) -> None:
        """Block until one token is available for `key` at `rate` tokens/
        second, then consume it.

        `rate <= 0` is treated as "no limit" (returns immediately) rather
        than raising or dividing by zero -- a connector or org budget of
        zero would otherwise deadlock every job for that key forever, which
        is a worse failure mode than simply not throttling an
        obviously-misconfigured rate.
        """
        if rate <= 0:
            return

        capacity = max(rate, 1.0)
        while True:
            async with self._lock:
                now = time.monotonic()
                tokens, last_refill = self._buckets.get(key, (min(rate, capacity), now))
                elapsed = now - last_refill
                tokens = min(capacity, tokens + elapsed * rate)

                if tokens >= 1.0:
                    self._buckets[key] = (tokens - 1.0, now)
                    return

                # Not enough tokens yet -- record the refill we just
                # accounted for, compute how long until one more token
                # accrues, and release the lock while waiting so other
                # keys' `acquire` calls aren't blocked behind this sleep.
                self._buckets[key] = (tokens, now)
                wait_seconds = (1.0 - tokens) / rate

            await asyncio.sleep(wait_seconds)


# One shared, in-process limiter for every job this worker process runs --
# module-level, not per-caller, so `ingestion.service._execute_ingestion_job`
# (the generic per-`fetch_batch` acquisition) and any connector that opts out
# of that (`Connector.rate_limits_own_requests = True`, e.g. `JiraConnector`,
# per this module's own "H2 fix" docstring note above) draw from the exact
# same buckets, not two independent ones that would silently double the real
# ceiling. Exposed as a plain module-level singleton (matching `app.database.
# session.engine`'s style) rather than an `@lru_cache`-wrapped accessor like
# `get_settings()`/`get_kms()`: those two read `Settings` at construction
# time and need to be re-constructible after a test monkeypatches an
# environment variable; `TokenBucketRateLimiter()` takes no such
# construction-time configuration (every `rate` is passed per-`acquire()`
# call), so there is nothing to invalidate.
_shared_rate_limiter = TokenBucketRateLimiter()


def get_ingestion_rate_limiter() -> TokenBucketRateLimiter:
    """Return the one `TokenBucketRateLimiter` instance every ingestion
    caller in this process shares.

    A function, not a bare module attribute import, so callers write
    `from app.ingestion.rate_limiter import get_ingestion_rate_limiter` --
    consistent with this codebase's existing singleton-accessor convention
    (`get_settings()`, `get_kms()`) and, unlike importing the module-level
    name directly, unambiguous about the fact that this returns a shared,
    stateful object rather than constructing a fresh one.
    """
    return _shared_rate_limiter
