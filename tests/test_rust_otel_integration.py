"""Opt-in end-to-end check for W3C propagation into a Rust control service."""

from __future__ import annotations

import os
import json
import socket
import subprocess
import threading
import time
import gzip
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("KAIROS_RUN_RUST_OTEL_INTEGRATION") != "1",
    reason="set KAIROS_RUN_RUST_OTEL_INTEGRATION=1 to run the Rust OTLP integration",
)


def test_rust_control_span_preserves_remote_trace_id(tmp_path: Path) -> None:
    pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    binary = Path(os.environ["KAIROS_RUST_OTEL_BIN"])
    assert binary.is_file(), f"Rust OTel binary does not exist: {binary}"
    received: list[bytes] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP method name is required
            payload = self.rfile.read(int(self.headers["content-length"]))
            if self.headers.get("content-encoding") == "gzip":
                payload = gzip.decompress(payload)
            if self.path == "/v1/traces":
                received.append(payload)
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    receiver_thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    receiver_thread.start()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workspace.toml").write_text(
        'version = 1\nworkspace_id = "otel-test"\n\n[cli]\nformat = "json"\n',
        encoding="utf-8",
    )
    trace_id = "0123456789abcdef0123456789abcdef"
    print(f"acceptance_trace_id={trace_id}")
    socket_path = _risk_socket_path(workspace)
    environment = {
        **os.environ,
        "KAIROS_OTEL_ENABLED": "1",
        "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{receiver.server_port}",
        "KAIROS_INSTANCE_ID": "instance",
        "KAIROS_WORKSPACE_ID": "otel-test",
        "KAIROS_LAUNCH_ID": "otel",
        "KAIROS_LAUNCH_MODE": "paper",
        "RUST_LOG": "info",
    }
    process = subprocess.Popen(
        [
            str(binary),
            "--workspace",
            str(workspace),
            "--launch-id",
            "otel",
            "--instance-id",
            "instance",
        ],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_socket(socket_path, process)
        _request(socket_path, "/v1/health", trace_id=trace_id)
        _request(
            socket_path,
            "/v1/not-found",
            trace_id=trace_id,
            expected_status=404,
        )
        _request(socket_path, "/v1/stop", method="POST", expected_status=202)
        assert process.wait(timeout=10) == 0
        deadline = time.monotonic() + 5
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
        exported = [
            ExportTraceServiceRequest.FromString(payload) for payload in received
        ]
        span_trace_ids = {
            span.trace_id.hex()
            for request in exported
            for resource_span in request.resource_spans
            for scope_span in resource_span.scope_spans
            for span in scope_span.spans
        }
        resource_attributes = {
            attribute.key: attribute.value.string_value
            for request in exported
            for resource_span in request.resource_spans
            for attribute in resource_span.resource.attributes
        }
        stdout = process.stdout.read() if process.stdout is not None else ""
        stderr = process.stderr.read() if process.stderr is not None else ""
        assert trace_id in span_trace_ids, (
            f"exported={span_trace_ids}, stdout={stdout}, stderr={stderr}"
        )
        assert trace_id in stderr
        log_records = [
            json.loads(line) for line in stderr.splitlines() if line.startswith("{")
        ]
        control_log = next(
            record
            for record in log_records
            if record.get("event") == "control_request_completed"
            and record.get("component") == "risk"
            and record["span"]["status"] == 200
        )
        span_fields = control_log["span"]
        assert span_fields["trace_id"] == trace_id
        assert len(span_fields["span_id"]) == 16
        assert span_fields["method"] == "GET"
        assert span_fields["path"] == "/v1/health"
        assert span_fields["status"] == 200
        assert control_log["level"] == "INFO"
        rejected_log = next(
            record
            for record in log_records
            if record.get("event") == "control_request_completed"
            and record.get("component") == "risk"
            and record["span"]["status"] == 404
        )
        assert rejected_log["span"]["trace_id"] == trace_id
        assert rejected_log["span"]["error_code"] == "control.request_rejected"
        assert rejected_log["span"]["retryable"] is False
        assert resource_attributes["service.name"] == "risk"
        assert resource_attributes["service.instance.id"] == "instance"
        assert resource_attributes["kairos.workspace_id"] == "otel-test"
        assert resource_attributes["kairos.launch_id"] == "otel"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        receiver.shutdown()
        receiver.server_close()
        receiver_thread.join()


def _wait_for_socket(socket_path: Path, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if socket_path.exists():
            return
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            pytest.fail(f"risk service exited before readiness: {stderr}")
        time.sleep(0.05)
    pytest.fail("risk service did not create its Unix socket")


def _risk_socket_path(workspace: Path) -> Path:
    instance_root = workspace / "launches" / "paper" / "otel" / "instances" / "instance"
    candidate = instance_root / "run" / "risk" / "control.sock"
    if len(str(candidate)) <= 100:
        return candidate
    digest = sha256(f"{instance_root}:risk".encode()).digest()[:10]
    return Path(f"/tmp/kairos-process-{digest.hex()}-risk.sock")


def _request(
    socket_path: Path,
    path: str,
    *,
    method: str = "GET",
    trace_id: str | None = None,
    expected_status: int = 200,
) -> None:
    headers = [f"{method} {path} HTTP/1.1", "Host: localhost", "Connection: close"]
    if trace_id is not None:
        headers.append(f"traceparent: 00-{trace_id}-0123456789abcdef-01")
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5)
    try:
        connection.connect(str(socket_path))
        connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode())
        assert int(connection.recv(128).split(maxsplit=2)[1]) == expected_status
    finally:
        connection.close()
