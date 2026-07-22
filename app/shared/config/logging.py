"""Structured logging configuration.

Owned by: shared/ (ARCHITECTURE.md section 3 -- cross-cutting, no business
meaning of its own, importable by every other module).

This is the second shared foundation piece after settings.py. Every module
that logs anything (core, agents, ingestion, mcp, retrieval) should call
`get_logger(__name__)` rather than importing `logging` or `structlog`
directly -- that's what keeps the log format/behavior consistent regardless
of which module is doing the logging, without every module needing to know
the configuration details below.

Two output modes, chosen by `Settings.environment`:
  - development / test -> human-readable, colored console output. Optimized
    for a solo developer reading logs in a terminal while iterating.
  - production          -> structured JSON, one event per line. Optimized for
    ingestion by a log aggregator, not for human eyes.

`Settings.log_level` (DATABASE/REDIS/etc. failures, agent errors, etc.) sets
the minimum level emitted, consistent with the settings module already
loaded before this one -- logging depends on settings, not the reverse.
"""

import logging
import sys

import structlog

from app.shared.config.settings import get_settings


def configure_logging() -> None:
    """Configure structlog + stdlib logging. Call once, at process startup.

    Safe to call multiple times (e.g. once from the API process, once from
    the arq worker entrypoint per ENGINEERING_DECISIONS.md #002) -- each call
    just re-applies the same configuration.
    """
    settings = get_settings()
    is_production = settings.environment == "production"

    # Shared processor chain: everything that adds context to a log event,
    # regardless of final rendering (JSON vs console).
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if is_production:
        renderer: structlog.types.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            *shared_processors,
            # Prepares the event dict for stdlib's formatter, which then
            # applies `renderer` below. This is what lets stdlib-originated
            # logs (uvicorn, sqlalchemy, arq) go through the same renderer
            # as structlog-originated ones, instead of looking different.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Strips structlog-internal metadata before final rendering, then
        # renders with whichever renderer was chosen above.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(settings.log_level)

    # Quiet down noisy third-party loggers that would otherwise clutter
    # output at INFO level (connection pool chatter, etc.) without hiding
    # anything we'd actually want to see during development.
    for noisy_logger in ("sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(noisy_logger).setLevel(
            logging.WARNING if not is_production else logging.ERROR
        )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to `name` (conventionally `__name__`).

    This is the only logging entrypoint other modules should use -- it
    guarantees every log event goes through the same configuration applied
    by `configure_logging()`, regardless of which module calls it.
    """
    return structlog.get_logger(name)

    """app/__init__.py
app/core/__init__.py
app/core/auth/__init__.py
app/core/users/__init__.py
app/core/incidents/__init__.py
app/core/audit/__init__.py
app/agents/__init__.py
app/agents/retrieval/__init__.py
app/agents/investigation/__init__.py
app/agents/postmortem/__init__.py
app/agents/knowledge_gap/__init__.py
app/mcp/__init__.py
app/mcp/servers/__init__.py
app/mcp/tools/__init__.py
app/mcp/resources/__init__.py
app/ingestion/__init__.py
app/ingestion/connectors/__init__.py
app/ingestion/processors/__init__.py
app/ingestion/workers/__init__.py
app/retrieval/__init__.py
app/retrieval/interfaces/__init__.py
app/retrieval/qdrant/__init__.py
app/retrieval/pgvector/__init__.py
app/retrieval/ranking/__init__.py
app/database/__init__.py
app/database/models/__init__.py
app/database/migrations/__init__.py
app/shared/__init__.py
app/shared/schemas/__init__.py
app/shared/config/__init__.py"""