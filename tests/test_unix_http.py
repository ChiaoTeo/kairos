from __future__ import annotations

import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path

import pytest

from kairospy.infrastructure.unix_http import _request_bytes, request_sync
from kairospy.strategy.logging import StrategyLogger


def test_request_sync_supports_unix_domain_socket(tmp_path: Path) -> None:
    # macOS limits AF_UNIX paths to a small fixed length.
    socket_path = Path(f"/tmp/kairos-http-{os.getpid()}.sock")
    socket_path.unlink(missing_ok=True)

    async def handler(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await reader.readuntil(b"\r\n\r\n")
        body = b'{"status":"ready"}'
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            + f"content-length: {len(body)}\r\n".encode()
            + b"content-type: application/json\r\n\r\n"
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def scenario() -> None:
        server = await asyncio.start_unix_server(handler, path=str(socket_path))
        try:
            status, value = await asyncio.to_thread(
                request_sync, socket_path, "GET", "/v1/health"
            )
            assert status == 200
            assert value == {"status": "ready"}
        finally:
            server.close()
            await server.wait_closed()
            socket_path.unlink(missing_ok=True)

    asyncio.run(scenario())


def test_request_injects_w3c_context_when_telemetry_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk")
    from kairospy.application.observability import configure_telemetry, start_span

    received: list[tuple[str, bytes]] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - HTTP method name is required
            size = int(self.headers["content-length"])
            received.append((self.path, self.rfile.read(size)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    receiver = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("KAIROS_OTEL_ENABLED", "1")
    monkeypatch.setenv("KAIROS_OTEL_TRACE_SAMPLE_RATIO", "1")
    endpoint = f"http://127.0.0.1:{receiver.server_port}/v1/traces"
    provider = configure_telemetry("unix-http-test", endpoint=endpoint)
    try:
        log_stream = StringIO()
        with start_span("client.operation"):
            request = _request_bytes("GET", "/v1/health", b"").decode("ascii")
            StrategyLogger(stream=log_stream).info(
                "signal accepted",
                event="strategy_signal_accepted",
                request_id="request-1",
            )
        traceparent = next(
            line for line in request.split("\r\n") if line.startswith("traceparent:")
        )
        assert traceparent.count("-") == 3
        log_record = json.loads(log_stream.getvalue())
        assert log_record["event"] == "strategy_signal_accepted"
        assert log_record["request_id"] == "request-1"
        assert len(log_record["trace_id"]) == 32
        assert len(log_record["span_id"]) == 16
        provider.force_flush()
        assert received and received[0][0] == "/v1/traces"
        assert received[0][1]
    finally:
        provider.shutdown()
        receiver.shutdown()
        receiver.server_close()
        thread.join()
