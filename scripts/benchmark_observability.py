#!/usr/bin/env python3
"""Compare Risk control-plane cost with telemetry disabled and enabled.

This intentionally uses only the public Unix HTTP boundary so it captures the
actual process, span creation and exporter path. Run it against a quiet local
Collector; do not use the result as a load-test substitute.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import statistics
import subprocess
import tempfile
import time
from hashlib import sha256
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=500)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--otlp-endpoint", default="http://127.0.0.1:4318")
    args = parser.parse_args()
    if args.rounds < 50:
        raise SystemExit("--rounds must be at least 50")
    result = {
        "schema_version": 1,
        "timestamp_unix": time.time(),
        "binary": str(args.binary.resolve()),
        "rounds": args.rounds,
        "disabled": _measure(args.binary, args.rounds, None),
        "enabled": _measure(args.binary, args.rounds, args.otlp_endpoint),
    }
    for metric in ("throughput_rps", "p95_ms", "rss_kib"):
        baseline = result["disabled"][metric]
        enabled = result["enabled"][metric]
        result[f"{metric}_change_percent"] = (
            (enabled - baseline) / baseline * 100 if baseline else None
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


def _measure(binary: Path, rounds: int, otlp_endpoint: str | None) -> dict[str, float]:
    with tempfile.TemporaryDirectory(prefix="kairos-observability-bench-") as raw:
        # Workspace::open canonicalizes its root before deriving the hashed
        # Unix-socket fallback. Do the same so /var -> /private/var aliases on
        # macOS do not produce a different hash.
        workspace = Path(raw).resolve()
        (workspace / "workspace.toml").write_text(
            'version = 1\nworkspace_id = "observability-benchmark"\n\n[cli]\nformat = "json"\n',
            encoding="utf-8",
        )
        launch = "enabled" if otlp_endpoint else "disabled"
        socket_path = _socket_path(workspace, launch)
        environment = os.environ | {
            "KAIROS_OTEL_ENABLED": "1" if otlp_endpoint else "0",
            "RUST_LOG": "warn",
        }
        if otlp_endpoint:
            environment["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_endpoint
        existing_fallback_sockets = set(
            Path("/tmp").glob("kairos-instance-*-risk.sock")
        )
        process = subprocess.Popen(
            [
                str(binary),
                "--workspace",
                str(workspace),
                "--launch-id",
                launch,
                "--instance-id",
                "bench",
            ],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            socket_path = _wait_for_socket(
                socket_path, process, workspace, existing_fallback_sockets
            )
            for _ in range(20):
                _request(socket_path)
            samples: list[float] = []
            started = time.perf_counter()
            for _ in range(rounds):
                request_started = time.perf_counter()
                _request(socket_path)
                samples.append((time.perf_counter() - request_started) * 1000)
            elapsed = time.perf_counter() - started
            rss_kib = float(
                subprocess.check_output(
                    ["ps", "-o", "rss=", "-p", str(process.pid)], text=True
                ).strip()
            )
            return {
                "throughput_rps": rounds / elapsed,
                "p95_ms": statistics.quantiles(samples, n=100)[94],
                "rss_kib": rss_kib,
            }
        finally:
            if process.poll() is None:
                if socket_path.exists():
                    _request(socket_path, method="POST", request_path="/v1/stop")
                process.wait(timeout=10)


def _socket_path(workspace: Path, launch: str) -> Path:
    candidate = (
        workspace
        / "launches"
        / "paper"
        / launch
        / "instances"
        / "bench"
        / "sockets"
        / "risk.sock"
    )
    if len(str(candidate)) <= 100:
        return candidate
    digest = sha256(f"{workspace}:paper:{launch}:bench:risk".encode()).digest()[:10]
    return Path(f"/tmp/kairos-instance-{digest.hex()}-risk.sock")


def _wait_for_socket(
    path: Path,
    process: subprocess.Popen[bytes],
    workspace: Path,
    existing_fallback_sockets: set[Path],
) -> Path:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return path
        workspace_sockets = list(workspace.rglob("risk.sock"))
        if workspace_sockets:
            return workspace_sockets[0]
        fallback_sockets = set(Path("/tmp").glob("kairos-instance-*-risk.sock"))
        created = fallback_sockets - existing_fallback_sockets
        if len(created) == 1:
            return created.pop()
        if process.poll() is not None:
            stderr = process.stderr.read().decode() if process.stderr else ""
            raise RuntimeError(
                f"Risk server exited before opening control socket: {stderr}"
            )
        time.sleep(0.01)
    raise TimeoutError("Risk server did not open control socket")


def _request(
    path: Path, *, method: str = "GET", request_path: str = "/v1/health"
) -> None:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5)
    try:
        connection.connect(str(path))
        connection.sendall(
            f"{method} {request_path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
        )
        if not connection.recv(128).split(maxsplit=2)[1].startswith(b"2"):
            raise RuntimeError("Risk control request failed")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
