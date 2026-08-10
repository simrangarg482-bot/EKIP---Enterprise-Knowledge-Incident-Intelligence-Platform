"""arq worker process entrypoint.

Owned by: ingestion/workers/. Run as its own OS process, separate from the
API server (PROJECT_PLAN.md section 4.5, ENGINEERING_DECISIONS.md #002):

    arq app.ingestion.workers.main.WorkerSettings

`redis_settings` is built from the same `Settings.redis_url` every other
part of the app reads (`app.shared.config.settings`) -- one source of truth
for the Redis connection string, not a second one hand-maintained here.
"""

from __future__ import annotations

from arq.connections import RedisSettings
from arq.cron import cron

from app.ingestion.workers.tasks import run_ingestion_job_task, scheduled_reconciliation
from app.shared.config.logging import configure_logging
from app.shared.config.settings import get_settings
from app.shared.config.tracing import configure_tracing

configure_logging()
configure_tracing()


class WorkerSettings:
    """arq's required entrypoint class -- discovered by name via the `arq`
    CLI command shown in this module's docstring.
    """

    functions = [run_ingestion_job_task]
    # Hourly reconciliation (minute=0, every hour) -- PROJECT_PLAN.md
    # section 4.4's "periodic reconciliation pass ... even for
    # webhook-supported sources". Omitting `hour` means "every hour", per
    # arq's cron field semantics (an omitted field matches any value, the
    # same convention as a bare `*` in crontab syntax).
    cron_jobs = [cron(scheduled_reconciliation, minute=0)]
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    # arq defaults every Worker to the same hardcoded queue name
    # ("arq:queue") regardless of `functions` -- with no override, this
    # worker and `app.agents.workers.main`'s worker share one Redis queue on
    # the same `Settings.redis_url`, so either one can pop a job it has no
    # matching function for (`JobExecutionFailed: function ... not found`,
    # a permanent, unretried failure) whenever both run at once, which is
    # the normal, documented deployment shape (both are expected to run
    # simultaneously). A distinct queue name per worker is required, not
    # cosmetic.
    queue_name = "arq:queue:ingestion"
    # Bounded max-attempt count (PROJECT_PLAN.md section 4.5). The
    # exponential backoff itself is implemented in
    # `run_ingestion_job_task` via `arq.jobs.Retry(defer=...)`, not here --
    # arq's own default retry has no backoff built in, so relying on
    # `max_tries` alone would satisfy only the "bounded" half of section
    # 4.5's requirement, not the "exponential backoff" half.
    max_tries = 3
    # arq's own default (300s) is tight for a first *full* sync: each
    # `fetch_batch` call is throttled by the per-connector token bucket in
    # `app.ingestion.rate_limiter` (e.g. Slack's own declared
    # `requests_per_second = 0.5`), and a channel/repo with real history
    # can need enough pages that the wait time alone exceeds 300s -- arq
    # then cancels the job mid-page rather than the connector or the app
    # failing outright. 30 minutes (the default) gives a real first sync
    # room to finish under that throttle instead of being treated as stuck.
    #
    # Sourced from `Settings.ingestion_job_timeout_seconds` (2026-08 audit
    # "H1" fix) rather than hardcoded here a second time -- `app.ingestion.
    # service._execute_ingestion_job` derives its own, slightly shorter
    # internal `asyncio.wait_for` timeout from the same setting, so this
    # value is the outer hard-kill backstop: it should only ever fire if
    # that internal timeout itself somehow fails to unwind cleanly (e.g. a
    # hung database write), not during an ordinary slow sync.
    job_timeout = get_settings().ingestion_job_timeout_seconds
