"""Optional OpenTelemetry bootstrap and propagation for Python processes."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from contextlib import nullcontext
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast


_DEFAULT_OTLP_BASE_ENDPOINT = "http://127.0.0.1:4318"
_telemetry_configured = False


@dataclass(frozen=True, slots=True)
class Telemetry:
    """Owns process-wide trace and metric providers for one Python process."""

    tracer_provider: Any
    meter_provider: Any

    def force_flush(self) -> bool:
        return bool(self.tracer_provider.force_flush()) and bool(
            self.meter_provider.force_flush()
        )

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()


def configure_telemetry(
    service_name: str,
    *,
    endpoint: str | None = None,
    metrics_endpoint: str | None = None,
    instance_id: str | None = None,
    workspace_id: str | None = None,
) -> Telemetry:
    """Install an OTLP tracer provider without making telemetry a hard dependency.

    The returned provider should be kept alive by the process owner. Export
    failures are handled by the SDK and must not be used as business-state
    control flow.
    """

    global _telemetry_configured

    from opentelemetry import metrics, trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    from opentelemetry.trace.propagation.tracecontext import (
        TraceContextTextMapPropagator,
    )

    attributes: dict[str, str] = {
        "service.name": service_name,
        "kairos.component": service_name,
    }
    if instance_id:
        attributes["service.instance.id"] = instance_id
        attributes["kairos.instance_id"] = instance_id
    if workspace_id:
        attributes["kairos.workspace_id"] = workspace_id
    for environment, attribute in (
        ("KAIROS_LAUNCH_ID", "kairos.launch_id"),
        ("KAIROS_LAUNCH_MODE", "kairos.launch_mode"),
        ("OTEL_DEPLOYMENT_ENVIRONMENT", "deployment.environment"),
    ):
        if value := os.getenv(environment):
            attributes[attribute] = value
    trace_exporter_kwargs = {} if endpoint is None else {"endpoint": endpoint}
    resolved_metrics_endpoint = metrics_endpoint or (
        endpoint.replace("/v1/traces", "/v1/metrics") if endpoint else None
    )
    metric_exporter_kwargs = (
        {}
        if resolved_metrics_endpoint is None
        else {"endpoint": resolved_metrics_endpoint}
    )
    resource = Resource.create(attributes)
    tracer_provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(_trace_sample_ratio())),
    )
    trace_exporter = cast(Any, OTLPSpanExporter)(**trace_exporter_kwargs)
    tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    meter_provider = MeterProvider(
        metric_readers=[
            PeriodicExportingMetricReader(
                cast(Any, OTLPMetricExporter)(**metric_exporter_kwargs)
            )
        ],
        resource=resource,
    )
    set_global_textmap(TraceContextTextMapPropagator())
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(set_logging_format=False)
    except ImportError:
        # The exporter remains usable when consumers deliberately install only
        # the base OTel packages rather than the optional logging integration.
        pass
    try:
        from opentelemetry.instrumentation.aiohttp_client import (
            AioHttpClientInstrumentor,
        )

        AioHttpClientInstrumentor().instrument()
    except ImportError:
        # HTTP instrumentation is optional for minimal command-line installs.
        pass
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    _telemetry_configured = True
    return Telemetry(tracer_provider, meter_provider)


def _trace_sample_ratio() -> float:
    try:
        ratio = float(os.getenv("KAIROS_OTEL_TRACE_SAMPLE_RATIO", "0.1"))
    except ValueError:
        return 0.1
    return ratio if 0.0 <= ratio <= 1.0 else 0.1


def configure_from_environment(
    service_name: str,
    *,
    instance_id: str | None = None,
    workspace_id: str | None = None,
) -> Telemetry | None:
    """Configure telemetry only when explicitly enabled by process configuration."""

    enabled = os.getenv("KAIROS_OTEL_ENABLED") == "1"
    endpoint = resolve_otlp_endpoint(
        os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
        enabled,
        "/v1/traces",
    )
    if endpoint is None:
        return None
    metrics_endpoint = resolve_otlp_endpoint(
        os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"),
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
        enabled,
        "/v1/metrics",
    )
    return configure_telemetry(
        service_name,
        endpoint=endpoint,
        metrics_endpoint=metrics_endpoint,
        instance_id=instance_id,
        workspace_id=workspace_id,
    )


def resolve_otlp_endpoint(
    signal_endpoint: str | None,
    generic_endpoint: str | None,
    enabled: bool,
    signal_path: str,
) -> str | None:
    """Resolve one OTLP/HTTP signal endpoint using the shared Kairos contract."""

    if signal_endpoint and signal_endpoint.strip():
        return signal_endpoint
    if generic_endpoint and generic_endpoint.strip():
        return f"{generic_endpoint.rstrip('/')}{signal_path}"
    if enabled:
        return f"{_DEFAULT_OTLP_BASE_ENDPOINT}{signal_path}"
    return None


def telemetry_enabled() -> bool:
    return _telemetry_configured or (
        resolve_otlp_endpoint(
            os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
            os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"),
            os.getenv("KAIROS_OTEL_ENABLED") == "1",
            "/v1/traces",
        )
        is not None
    )


def inject_trace_headers(headers: MutableMapping[str, str]) -> None:
    """Inject the current W3C trace context into an outbound HTTP carrier.

    This remains a no-op when observability is not installed or enabled, so
    ordinary command transports do not gain a runtime dependency on OTel.
    """

    if not telemetry_enabled():
        return
    from opentelemetry.propagate import inject

    inject(headers)


def start_span(name: str, *, attributes: dict[str, str] | None = None) -> Any:
    """Return a current-span context manager when telemetry is enabled."""

    if not telemetry_enabled():
        return nullcontext()
    from opentelemetry import trace

    return trace.get_tracer("kairospy").start_as_current_span(
        name, attributes=attributes
    )


@lru_cache(maxsize=64)
def _counter(name: str) -> Any:
    from opentelemetry import metrics

    return metrics.get_meter("kairospy").create_counter(name)


@lru_cache(maxsize=64)
def _histogram(name: str) -> Any:
    from opentelemetry import metrics

    return metrics.get_meter("kairospy").create_histogram(name, unit="ms")


@lru_cache(maxsize=64)
def _gauge(name: str) -> Any:
    from opentelemetry import metrics

    return metrics.get_meter("kairospy").create_gauge(name)


def record_counter(name: str, value: int = 1) -> None:
    """Record a monotonic process metric without adding a business dependency."""

    if telemetry_enabled():
        _counter(name).add(value)


def record_duration_ms(name: str, value: float) -> None:
    """Record a duration in milliseconds at an application boundary."""

    if telemetry_enabled():
        _histogram(name).record(value)


def record_gauge(name: str, value: int | float) -> None:
    """Record a point-in-time gauge without affecting business control flow."""

    if telemetry_enabled():
        _gauge(name).set(value)


__all__ = [
    "configure_from_environment",
    "configure_telemetry",
    "inject_trace_headers",
    "record_counter",
    "record_duration_ms",
    "record_gauge",
    "resolve_otlp_endpoint",
    "start_span",
    "telemetry_enabled",
]
