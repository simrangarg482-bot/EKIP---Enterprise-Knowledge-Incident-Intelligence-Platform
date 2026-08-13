"""Worker queue isolation and Redis DSN handling.

Covers two production failures found during live validation. Both are
configuration-shaped bugs that no connector or service test could catch,
because they live in the arq `WorkerSettings` classes and the Redis DSN
plumbing rather than in any request path.

1. QUEUE COLLISION
   arq defaults every Worker to the same hardcoded queue name regardless of
   which `functions` it registers. `app.ingestion.workers.main` and
   `app.agents.workers.main` both took that default while pointing at one
   Redis instance, so whichever worker polled first popped whatever job was
   queued -- including jobs for functions it had never registered. The
   agents worker would grab `run_ingestion_job_task`, fail it permanently
   with `JobExecutionFailed: function not found` (arq does not requeue a
   job it cannot resolve), and the ingestion job simply vanished. Both
   workers running at once is the normal deployment shape, so this was not
   an edge case.

2. redis:// vs rediss://
   Workers could not reach Redis at all. The root cause was a `.env` URL
   using the TLS scheme against a plaintext endpoint, NOT a code defect --
   `RedisSettings.from_dsn` maps the scheme to the TLS flag correctly in
   both directions. The tests below pin that mapping so a future change
   that drops the scheme, hardcodes `ssl`, or normalises the URL cannot
   silently reintroduce a connection failure that looks like this one.
"""

from __future__ import annotations

import inspect

import pytest
from arq.connections import RedisSettings
from pydantic import RedisDsn, TypeAdapter

from app.agents.workers.main import WorkerSettings as AgentsWorkerSettings
from app.api import main as api_main
from app.ingestion.workers.main import WorkerSettings as IngestionWorkerSettings

_ALL_WORKERS = (IngestionWorkerSettings, AgentsWorkerSettings)


# --- queue isolation ------------------------------------------------------


def test_workers_do_not_share_a_queue() -> None:
    queue_names = [settings.queue_name for settings in _ALL_WORKERS]
    assert len(set(queue_names)) == len(queue_names), (
        f"workers share a queue name {queue_names!r}; with one Redis instance either worker can "
        "pop the other's jobs and fail them permanently with 'function not found'"
    )


def test_every_worker_sets_an_explicit_queue_name() -> None:
    """arq's default is a single shared constant, so relying on it is what
    caused the collision -- each worker must opt out of it explicitly.
    """
    for settings in _ALL_WORKERS:
        queue_name = getattr(settings, "queue_name", None)
        assert queue_name, f"{settings.__module__} does not set an explicit queue_name"
        assert queue_name != "arq:queue", f"{settings.__module__} is still on arq's default queue"


def test_worker_function_sets_are_disjoint() -> None:
    """Why sharing a queue is actively harmful rather than merely untidy: no
    function is registered on both workers, so a misrouted job can never be
    executed by the worker that received it.
    """
    registered = [
        {getattr(fn, "__name__", str(fn)) for fn in settings.functions} for settings in _ALL_WORKERS
    ]
    assert not registered[0] & registered[1], (
        f"workers share registered functions {registered[0] & registered[1]!r} -- "
        "this test's assumption about misrouted jobs being unexecutable no longer holds"
    )


def test_queue_names_are_namespaced_per_worker() -> None:
    ingestion = IngestionWorkerSettings.queue_name
    agents = AgentsWorkerSettings.queue_name
    assert "ingestion" in ingestion, f"ingestion queue name {ingestion!r} is not self-describing"
    assert "agents" in agents, f"agents queue name {agents!r} is not self-describing"


# --- Redis DSN scheme -> TLS ---------------------------------------------


@pytest.mark.parametrize(
    ("dsn", "expected_ssl"),
    [
        ("redis://default:pw@redis.example.com:6379", False),
        ("rediss://default:pw@redis.example.com:6379", True),
        ("redis://redis.example.com:6379/2", False),
        ("rediss://redis.example.com:6379/2", True),
    ],
)
def test_dsn_scheme_controls_tls(dsn: str, expected_ssl: bool) -> None:
    """`rediss://` must enable TLS and `redis://` must not. Getting this
    backwards produces the two failure modes seen in the wild: an
    `[SSL: WRONG_VERSION_NUMBER]` handshake error against a plaintext
    endpoint, or an unencrypted connection to a TLS-only one.
    """
    assert RedisSettings.from_dsn(dsn).ssl is expected_ssl


def test_dsn_preserves_host_port_and_database() -> None:
    settings = RedisSettings.from_dsn("rediss://default:pw@redis.example.com:13453/3")
    assert settings.host == "redis.example.com"
    assert settings.port == 13453
    assert settings.database == 3


@pytest.mark.parametrize("scheme", ["redis", "rediss"])
def test_settings_redis_url_accepts_both_schemes(scheme: str) -> None:
    """The app validates `Settings.redis_url` as a pydantic `RedisDsn`
    before arq ever sees it, so a scheme rejected here would fail at
    startup rather than at connect time.
    """
    adapter = TypeAdapter(RedisDsn)
    assert adapter.validate_python(f"{scheme}://default:pw@redis.example.com:6379")


def test_both_workers_build_redis_settings_from_the_same_configured_url() -> None:
    """One source of truth: both workers must resolve to the same Redis
    host/port, or 'the queues are isolated' would be trivially true for the
    wrong reason -- they would simply be on different servers.
    """
    ingestion = IngestionWorkerSettings.redis_settings
    agents = AgentsWorkerSettings.redis_settings
    assert (ingestion.host, ingestion.port) == (agents.host, agents.port)


# --- producer/consumer queue agreement ------------------------------------


def test_api_lifespan_enqueues_onto_the_ingestion_worker_queue() -> None:
    """The tests above only check the two `WorkerSettings` classes against
    each other -- they say nothing about whether `app.api.main`'s arq pool
    (the thing `POST /tenancy/connectors/{id}/sync` actually enqueues onto)
    targets a queue either worker is listening to.

    `arq.create_pool`'s own default (`default_queue_name="arq:queue"`) is
    exactly the shared default both `WorkerSettings` classes above
    deliberately opt out of -- so a pool created without an explicit
    `default_queue_name` enqueues onto a queue neither worker polls, and a
    connector "sync" click enqueues a job that sits in Redis forever,
    silently never running. `run_ingestion_job_task` is the only function
    the API's pool ever enqueues (`app.api.routers.tenancy.sync_connector`),
    so the pool's queue must match the worker that registers that function.
    """
    source = inspect.getsource(api_main._lifespan)
    assert 'default_queue_name="arq:queue:ingestion"' in source, (
        "app.api.main's arq pool must pass default_queue_name="
        f"{IngestionWorkerSettings.queue_name!r} to create_pool(...) -- "
        "otherwise it silently falls back to arq's own default queue "
        "('arq:queue'), which the ingestion worker never polls, and "
        "connector syncs enqueue jobs that are never executed"
    )
