"""OpenTelemetry tracing configuration (Advanced Features Roadmap Phase 1,
"OTel tracing (2.3)"): `opentelemetry-sdk` has been a declared dependency
since project inception (`pyproject.toml`) but nothing in this codebase ever
configured a `TracerProvider` or created a span -- this module is what
finally wires it for real, the tracing equivalent of `logging.py`'s
`configure_logging()`.

Owned by: shared/ (ARCHITECTURE.md section 3 -- cross-cutting, no business
meaning of its own, importable by every other module) -- same ownership
`logging.py` already establishes for the identical reason.

Two export modes, chosen by whether `Settings.otel_exporter_otlp_endpoint`
is set:
  - unset (the default) -> `ConsoleSpanExporter`, printing each finished
    span to stdout. Requires no external collector -- the same "optimized
    for a solo developer iterating locally" default `logging.py`'s own
    module docstring describes for its console log renderer.
  - set -> OTLP/HTTP export to that endpoint (a self-hosted Jaeger/Tempo/
    Grafana stack, or a hosted LLM-observability tool accepting OTLP).
    Phase 2's Docker/deployment story (not yet built) is what would stand up
    a real collector; this module only needs to know where to send spans
    once one exists, so the wiring is ready ahead of that.

`configure_tracing()` is called at the exact same process-startup points
`configure_logging()` already is: `app/agents/workers/main.py`,
`app/ingestion/workers/main.py`, `scripts/seed_test_organization.py`,
`scripts/run_evaluation.py` -- plus `app/api/main.py`, which does not
currently call `configure_logging()` at all (a pre-existing gap this change
does not fix, out of this level's scope) but does need `configure_tracing()`
for `FastAPIInstrumentor.instrument_app(app)` (see that module).
"""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)

from app.shared.config.logging import get_logger
from app.shared.config.settings import Settings, get_settings

logger = get_logger(__name__)

_configured = False


def configure_tracing() -> None:
    """Configure the global OTel `TracerProvider`. Call once, at process
    startup.

    Safe to call multiple times -- mirrors `configure_logging()`'s identical
    guarantee -- via an explicit module-level guard rather than relying on
    the OTel SDK's own "a second `set_tracer_provider` call just logs a
    warning and is ignored" behavior: constructing a whole new
    `Resource`/`TracerProvider`/exporter/processor on every call would be
    wasted work even though it would ultimately be a no-op.

    No-ops (past logging that it's disabled) when `Settings.otel_enabled` is
    `False` -- every `app.agents.tracing.traced_node`-wrapped node call
    still runs completely normally either way; it just records to
    whichever no-op tracer the OTel API defaults to when no provider has
    ever been set, which is itself a valid, zero-overhead "tracing is off"
    state, not a second code path this module needs to maintain.
    """
    global _configured
    if _configured:
        return

    settings = get_settings()
    if not settings.otel_enabled:
        logger.info("otel_tracing_disabled")
        _configured = True
        return

    resource = Resource.create({SERVICE_NAME: settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(_build_exporter(settings)))
    trace.set_tracer_provider(provider)
    _configured = True

    logger.info(
        "otel_tracing_configured",
        service_name=settings.otel_service_name,
        exporter="otlp" if settings.otel_exporter_otlp_endpoint else "console",
    )


def _build_exporter(settings: Settings) -> SpanExporter:
    """`ConsoleSpanExporter` needs no extra dependency (ships inside
    `opentelemetry-sdk` itself, already declared). The OTLP exporter is a
    separate, NOT currently declared package
    (`opentelemetry-exporter-otlp-proto-http`) -- imported lazily here, only
    when an endpoint is actually configured, with a clear error if it isn't
    installed, rather than forcing every environment that never sets
    `otel_exporter_otlp_endpoint` to install it for no benefit.
    """
    endpoint = settings.otel_exporter_otlp_endpoint
    if not endpoint:
        return ConsoleSpanExporter()

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
    except ImportError as exc:
        raise RuntimeError(
            "Settings.otel_exporter_otlp_endpoint is set but the "
            "'opentelemetry-exporter-otlp-proto-http' package is not installed. "
            "Install it (`pip install opentelemetry-exporter-otlp-proto-http`) "
            "or unset OTEL_EXPORTER_OTLP_ENDPOINT to fall back to console export."
        ) from exc

    return OTLPSpanExporter(endpoint=endpoint)
