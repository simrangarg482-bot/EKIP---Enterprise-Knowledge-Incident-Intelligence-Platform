"""arq worker process entrypoint for agents/'s scheduled agents. Run as its
own process, separate from both the API server and the ingestion worker:

    arq app.agents.workers.main.WorkerSettings

`redis_settings` is built from the same `Settings.redis_url` every other
part of the app reads, the same single-source-of-truth convention
`app.ingestion.workers.main`'s identical line already follows.
"""

from __future__ import annotations

from arq.connections import RedisSettings
from arq.cron import cron

from app.agents.workers.tasks import run_knowledge_gap_detection_task, scheduled_knowledge_gap_scan
from app.shared.config.logging import configure_logging
from app.shared.config.settings import get_settings
from app.shared.config.tracing import configure_tracing

configure_logging()
configure_tracing()


class WorkerSettings:
    """arq's required entrypoint class -- discovered by name via the `arq`
    CLI command shown in this module's docstring.
    """

    functions = [run_knowledge_gap_detection_task]
    # Daily at 02:00 -- deliberately much less frequent than ingestion's
    # hourly reconciliation: a documentation gap is, by definition, a
    # *repeated* pattern accumulated over `knowledge_gap_lookback_days`
    # (default 14) of history, not something that meaningfully changes
    # hour to hour the way "has a new commit landed" does. Running hourly
    # would mostly re-scan the same low-confidence executions and re-merge
    # into the same open reports for no benefit.
    cron_jobs = [cron(scheduled_knowledge_gap_scan, hour=2, minute=0)]
    redis_settings = RedisSettings.from_dsn(str(get_settings().redis_url))
    # See `app.ingestion.workers.main.WorkerSettings.queue_name`'s comment --
    # without an explicit, distinct queue name here too, this worker shares
    # arq's default queue with the ingestion worker on the same Redis
    # instance and can steal its jobs, failing them permanently.
    queue_name = "arq:queue:agents"
    # Same bounded max-attempt count as `app.ingestion.workers.main` -- see
    # that class's own comment on why the backoff itself lives in the task
    # function (`Retry(defer=...)`), not here.
    max_tries = 3
