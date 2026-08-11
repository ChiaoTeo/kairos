"""Opt-in smoke check for the local OpenTelemetry Collector stack."""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("KAIROS_RUN_COLLECTOR_SMOKE") != "1",
    reason="set KAIROS_RUN_COLLECTOR_SMOKE=1 after starting the local Collector",
)


def test_collector_accepts_otlp_http_trace() -> None:
    trace_service = pytest.importorskip(
        "opentelemetry.proto.collector.trace.v1.trace_service_pb2"
    )
    request = trace_service.ExportTraceServiceRequest()
    resource_span = request.resource_spans.add()
    resource = resource_span.resource
    attribute = resource.attributes.add()
    attribute.key = "service.name"
    attribute.value.string_value = "collector-smoke"
    span = resource_span.scope_spans.add().spans.add()
    span.trace_id = bytes.fromhex("0123456789abcdef0123456789abcdef")
    span.span_id = bytes.fromhex("0123456789abcdef")
    span.name = "collector.smoke"
    span.start_time_unix_nano = time.time_ns()
    span.end_time_unix_nano = time.time_ns()
    endpoint = os.getenv(
        "KAIROS_COLLECTOR_TRACES_ENDPOINT", "http://127.0.0.1:4318/v1/traces"
    )
    response = urlopen(
        Request(
            endpoint,
            data=request.SerializeToString(),
            method="POST",
            headers={"Content-Type": "application/x-protobuf"},
        ),
        timeout=5,
    )
    assert response.status == 200
    _wait_for_tempo_trace("0123456789abcdef0123456789abcdef")


def test_collector_exports_otlp_metrics_to_prometheus() -> None:
    metrics_service = pytest.importorskip(
        "opentelemetry.proto.collector.metrics.v1.metrics_service_pb2"
    )
    request = metrics_service.ExportMetricsServiceRequest()
    metric = request.resource_metrics.add().scope_metrics.add().metrics.add()
    metric.name = "kairos_smoke_gauge"
    metric.gauge.data_points.add().as_int = 1
    response = urlopen(
        Request(
            os.getenv(
                "KAIROS_COLLECTOR_METRICS_ENDPOINT",
                "http://127.0.0.1:4318/v1/metrics",
            ),
            data=request.SerializeToString(),
            method="POST",
            headers={"Content-Type": "application/x-protobuf"},
        ),
        timeout=5,
    )
    assert response.status == 200
    _wait_for_metric("kairos_smoke_gauge")


def test_collector_health_and_metrics_endpoints_are_available() -> None:
    health = urlopen("http://127.0.0.1:13133/", timeout=5)
    assert health.status == 200


def test_collector_exports_json_log_to_loki() -> None:
    root = os.getenv("KAIROS_LOG_ROOT")
    if root is None:
        pytest.skip("set KAIROS_LOG_ROOT to the mounted Collector log directory")
    log_path = Path(root) / "e2e" / "collector-smoke.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        '{"schema_version":1,"system_time":"2026-08-10T00:00:00Z",'
        '"level":"info","event":"collector_log_smoke",'
        '"component":"strategy","process_id":"strategy",'
        '"instance_id":"e2e","workspace_id":"e2e",'
        '"trace_id":"0123456789abcdef0123456789abcdef",'
        '"span_id":"0123456789abcdef","request_id":"e2e",'
        '"duration_ms":1,"result":"accepted"}\n',
        encoding="utf-8",
    )
    deadline = time.monotonic() + 15
    while True:
        payload = _loki_query('{service_name=~".+"} |= "collector_log_smoke"')
        if payload["data"]["result"]:
            return
        if time.monotonic() >= deadline:
            pytest.fail("Loki did not return the Collector-ingested JSON log")
        time.sleep(0.2)


def _wait_for_metric(name: str) -> None:
    deadline = time.monotonic() + 15
    while True:
        metrics = urlopen("http://127.0.0.1:8889/metrics", timeout=5).read().decode()
        if name in metrics:
            return
        if time.monotonic() >= deadline:
            pytest.fail(f"Collector metrics endpoint did not expose {name}")
        time.sleep(0.2)


def _wait_for_tempo_trace(trace_id: str) -> None:
    deadline = time.monotonic() + 15
    while True:
        try:
            payload = urlopen(
                f"http://127.0.0.1:3200/api/traces/{trace_id}", timeout=5
            ).read()
        except HTTPError as error:
            if error.code != 404:
                raise
        else:
            if b"collector.smoke" in payload and b"collector-smoke" in payload:
                return
        if time.monotonic() >= deadline:
            pytest.fail("Tempo did not return the Collector-exported trace")
        time.sleep(0.2)


def _loki_query(query: str) -> dict[str, object]:
    from json import loads
    from urllib.parse import urlencode

    url = f"http://127.0.0.1:3100/loki/api/v1/query_range?{urlencode({'query': query})}"
    return loads(urlopen(url, timeout=5).read())
