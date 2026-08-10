"""Unit tests for `app.shared.config.tracing`.

Deliberately never calls the real, process-global `opentelemetry.trace.
set_tracer_provider` -- that function may only succeed once per process, so
a test that actually invoked it could pollute every other test in the same
pytest run. Instead, the "enabled" path is verified by monkeypatching
`tracing.trace.set_tracer_provider` itself to a recording fake.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

from app.shared.config import tracing


class _FakeSettings:
    def __init__(
        self,
        *,
        otel_enabled: bool = True,
        otel_service_name: str = "ekip-test",
        otel_exporter_otlp_endpoint: str | None = None,
    ) -> None:
        self.otel_enabled = otel_enabled
        self.otel_service_name = otel_service_name
        self.otel_exporter_otlp_endpoint = otel_exporter_otlp_endpoint


@pytest.fixture(autouse=True)
def _reset_configured_guard(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tracing, "_configured", False)
    yield
    monkeypatch.setattr(tracing, "_configured", False)


def test_build_exporter_defaults_to_console_when_no_endpoint_configured() -> None:
    exporter = tracing._build_exporter(_FakeSettings(otel_exporter_otlp_endpoint=None))  # type: ignore[arg-type]
    assert isinstance(exporter, ConsoleSpanExporter)


def test_build_exporter_raises_clear_error_when_otlp_package_missing() -> None:
    """`opentelemetry-exporter-otlp-proto-http` is deliberately NOT a
    declared dependency (see `tracing.py`'s own docstring) -- setting an
    endpoint without it installed must fail with a clear, actionable error,
    not an opaque `ImportError`. This exercises the real import path (the
    package genuinely isn't installed in this project), not a mock.
    """
    with pytest.raises(RuntimeError, match="opentelemetry-exporter-otlp-proto-http"):
        tracing._build_exporter(  # type: ignore[arg-type]
            _FakeSettings(otel_exporter_otlp_endpoint="http://localhost:4318/v1/traces")
        )


def test_configure_tracing_logs_and_noops_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "get_settings", lambda: _FakeSettings(otel_enabled=False))
    set_provider_calls: list[object] = []
    monkeypatch.setattr(tracing.trace, "set_tracer_provider", set_provider_calls.append)

    tracing.configure_tracing()

    assert tracing._configured is True
    assert set_provider_calls == []  # disabled path must never touch the global provider


def test_configure_tracing_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    call_count = 0

    def fake_get_settings() -> _FakeSettings:
        nonlocal call_count
        call_count += 1
        return _FakeSettings(otel_enabled=False)

    monkeypatch.setattr(tracing, "get_settings", fake_get_settings)

    tracing.configure_tracing()
    tracing.configure_tracing()

    assert call_count == 1  # the second call short-circuits before touching settings at all


def test_configure_tracing_sets_a_tracer_provider_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "get_settings", lambda: _FakeSettings(otel_enabled=True))
    recorded: dict[str, object] = {}
    monkeypatch.setattr(
        tracing.trace, "set_tracer_provider", lambda provider: recorded.__setitem__("provider", provider)
    )

    tracing.configure_tracing()

    assert "provider" in recorded
    assert tracing._configured is True
