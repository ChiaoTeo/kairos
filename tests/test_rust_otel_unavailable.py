"""Collector failure must not affect Rust control-plane availability."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("KAIROS_RUN_RUST_OTEL_INTEGRATION") != "1",
    reason="run in the OTLP integration CI job",
)


def test_risk_control_service_runs_when_collector_is_unavailable(
    tmp_path: Path,
) -> None:
    _assert_risk_control_survives_exporter_failure(tmp_path, "http://127.0.0.1:9")


def test_risk_control_service_runs_when_collector_returns_5xx(tmp_path: Path) -> None:
    class FailingReceiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP method name is required
            self.rfile.read(int(self.headers["content-length"]))
            self.send_response(503)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), FailingReceiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    try:
        _assert_risk_control_survives_exporter_failure(
            tmp_path, f"http://127.0.0.1:{receiver.server_port}"
        )
    finally:
        receiver.shutdown()
        receiver.server_close()


def test_risk_control_service_runs_when_collector_times_out(tmp_path: Path) -> None:
    class SlowReceiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP method name is required
            self.rfile.read(int(self.headers["content-length"]))
            time.sleep(2)
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), SlowReceiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    try:
        _assert_risk_control_survives_exporter_failure(
            tmp_path, f"http://127.0.0.1:{receiver.server_port}"
        )
    finally:
        receiver.shutdown()
        receiver.server_close()


def _assert_risk_control_survives_exporter_failure(
    tmp_path: Path, endpoint: str
) -> None:
    binary = Path(os.environ["KAIROS_RUST_OTEL_BIN"])
    assert binary.is_file()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "workspace.toml").write_text(
        'version = 1\nworkspace_id = "otel-unavailable"\n\n[cli]\nformat = "json"\n',
        encoding="utf-8",
    )
    socket_path = _risk_socket_path(workspace)
    process = subprocess.Popen(
        [
            str(binary),
            "--workspace",
            str(workspace),
            "--launch-id",
            "otel-unavailable",
            "--instance-id",
            "instance",
        ],
        env={
            **os.environ,
            "KAIROS_OTEL_ENABLED": "1",
            "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
            "RUST_LOG": "info",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_socket(socket_path, process)
        _request(socket_path, "/v1/health")
        _request(socket_path, "/v1/stop", method="POST")
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


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
    candidate = (
        workspace
        / "launches"
        / "paper"
        / "otel-unavailable"
        / "instances"
        / "instance"
        / "sockets"
        / "risk.sock"
    )
    if len(str(candidate)) <= 100:
        return candidate
    digest = sha256(
        f"{workspace}:paper:otel-unavailable:instance:risk".encode()
    ).digest()[:10]
    return Path(f"/tmp/kairos-instance-{digest.hex()}-risk.sock")


def _request(socket_path: Path, path: str, *, method: str = "GET") -> None:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5)
    try:
        connection.connect(str(socket_path))
        connection.sendall(
            (
                f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            ).encode()
        )
        assert connection.recv(128).split(maxsplit=2)[1].startswith(b"2")
    finally:
        connection.close()
