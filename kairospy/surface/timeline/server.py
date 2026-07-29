from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
from typing import Any
from urllib.parse import urlparse
import webbrowser

from kairospy.surface.rendering.writer import jsonable

from .assets import CSS, HTML, JS
from .loader import TimelineDataLoader


def serve_timeline(
    instance_path: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
) -> dict[str, object]:
    data = TimelineDataLoader(instance_path).load()
    selected_port = _available_port(host, port)
    server = _TimelineServer((host, selected_port), _handler(data))
    url = f"http://{host}:{selected_port}"
    if open_browser:
        webbrowser.open(url)
    try:
        print(f"Timeline Viewer {url}")
        print(f"Run instance {data['instance']['path']}")
        print("Press Ctrl-C to stop.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTimeline Viewer stopped.")
    finally:
        server.server_close()
    return {"url": url, "instance": data["instance"]}


class _TimelineServer(ThreadingHTTPServer):
    daemon_threads = True


def _handler(data: dict[str, object]) -> type[BaseHTTPRequestHandler]:
    class TimelineHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path == "/":
                self._send_text(HTML, "text/html; charset=utf-8")
                return
            if path == "/style.css":
                self._send_text(CSS, "text/css; charset=utf-8")
                return
            if path == "/app.js":
                self._send_text(JS, "application/javascript; charset=utf-8")
                return
            if path == "/api/timeline":
                self._send_json(data)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_text(self, body: str, content_type: str) -> None:
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, body: object) -> None:
            encoded = json.dumps(jsonable(body), ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return TimelineHandler


def _available_port(host: str, preferred: int) -> int:
    if preferred == 0:
        return _bind_free_port(host)
    try:
        with socket.create_connection((host, preferred), timeout=0.2):
            return _bind_free_port(host)
    except OSError:
        return preferred


def _bind_free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])

