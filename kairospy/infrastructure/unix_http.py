"""HTTP transport over Unix domain sockets."""

from __future__ import annotations

import json
import asyncio
import socket
from pathlib import Path
from typing import Any, Mapping


def request_sync(
    socket_path: str | Path,
    method: str,
    path: str,
    body: Mapping[str, Any] | bytes | None = None,
    *,
    timeout: float = 3.0,
) -> tuple[int, dict[str, Any]]:
    payload = _encode_body(body)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    connection.connect(str(socket_path))
    with connection:
        connection.sendall(_request_bytes(method, path, payload))
        status, response_body = _read_response(connection)
    return _decode_json_response(status, response_body)


async def request_async(
    socket_path: str | Path,
    method: str,
    path: str,
    body: bytes | None = None,
    *,
    timeout: float = 3.0,
) -> dict[str, Any]:
    payload = _encode_body(body)
    reader, writer = await asyncio.wait_for(
        asyncio.open_unix_connection(str(socket_path)), timeout=timeout
    )
    try:
        writer.write(_request_bytes(method, path, payload))
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        status, response_body = await _read_response_async(reader, timeout)
    finally:
        writer.close()
        await writer.wait_closed()
    status, value = _decode_json_response(status, response_body)
    if not 200 <= status < 300:
        raise RuntimeError(f"Unix HTTP request failed ({status}): {value}")
    return value


def _encode_body(body: Mapping[str, Any] | bytes | None) -> bytes:
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def _request_bytes(method: str, path: str, payload: bytes) -> bytes:
    headers = [
        f"{method.upper()} {path} HTTP/1.1",
        "Host: localhost",
        "Connection: close",
    ]
    if payload:
        headers.extend(("content-type: application/json", f"content-length: {len(payload)}"))
    return ("\r\n".join(headers) + "\r\n\r\n").encode("ascii") + payload


def _decode_json_response(status: int, body: bytes) -> tuple[int, dict[str, Any]]:
    value = json.loads(body) if body else {}
    if not isinstance(value, dict):
        raise ValueError("Unix HTTP response must be a JSON object")
    return status, value


def _read_response(connection: socket.socket) -> tuple[int, bytes]:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        data.extend(connection.recv(4096))
    header_bytes, body = bytes(data).split(b"\r\n\r\n", 1)
    status, headers = _parse_headers(header_bytes)
    return status, _read_body(connection.recv, body, headers)


async def _read_response_async(reader: asyncio.StreamReader, timeout: float) -> tuple[int, bytes]:
    header_bytes = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=timeout)
    status, headers = _parse_headers(header_bytes[:-4])
    if "content-length" in headers:
        size = int(headers["content-length"])
        body = await asyncio.wait_for(reader.readexactly(size), timeout=timeout) if size else b""
    else:
        body = await asyncio.wait_for(reader.read(), timeout=timeout)
    return status, body


def _parse_headers(header_bytes: bytes) -> tuple[int, dict[str, str]]:
    lines = header_bytes.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {
        name.strip().lower(): value.strip()
        for name, value in (line.split(":", 1) for line in lines[1:] if ":" in line)
    }
    return status, headers


def _read_body(recv: Any, initial: bytes, headers: dict[str, str]) -> bytes:
    if "content-length" in headers:
        size = int(headers["content-length"])
        body = initial[:size]
        while len(body) < size:
            body += recv(size - len(body))
        return body
    chunks = [initial]
    while True:
        chunk = recv(4096)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
