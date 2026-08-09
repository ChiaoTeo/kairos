from __future__ import annotations

import asyncio
import os
from pathlib import Path

from kairospy.infrastructure.unix_http import request_sync


def test_request_sync_supports_unix_domain_socket(tmp_path: Path) -> None:
    # macOS limits AF_UNIX paths to a small fixed length.
    socket_path = Path(f"/tmp/kairos-http-{os.getpid()}.sock")
    socket_path.unlink(missing_ok=True)

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
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
