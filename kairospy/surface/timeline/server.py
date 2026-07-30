from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import json
from pathlib import Path
import socket
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode, urlparse
import webbrowser

from .assets import CSS, HTML, JS
from .loader import TimelineDataLoader, find_latest_instance, list_instances


def serve_timeline(
    run_root: Path,
    *,
    selected_instance_path: Path | None = None,
    mode: str | None = None,
    run_id: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    static_root: Path | None = None,
) -> dict[str, object]:
    root = run_root.expanduser().resolve()
    default_instance = _default_instance(root, selected_instance_path=selected_instance_path, mode=mode, run_id=run_id)
    selected_port = _available_port(host, port)
    server = _TimelineServer(
        (host, selected_port),
        _handler(root, default_instance=default_instance, static_root=_valid_static_root(static_root)),
    )
    url = _viewer_url(host, selected_port, default_instance)
    if open_browser:
        webbrowser.open(url)
    try:
        print(f"Timeline Viewer {url}")
        print(f"Run root {root}")
        if default_instance is not None:
            print(f"Default run instance {default_instance}")
        print("Press Ctrl-C to stop.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTimeline Viewer stopped.")
    finally:
        server.server_close()
    return {"url": url, "root": str(root), "default_instance": str(default_instance) if default_instance else None}


class _TimelineServer(ThreadingHTTPServer):
    daemon_threads = True


def _handler(root: Path, *, default_instance: Path | None, static_root: Path | None) -> type[BaseHTTPRequestHandler]:
    class TimelineHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self._route(send_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._route(send_body=False)

        def _route(self, *, send_body: bool) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            if path == "/api/instances":
                self._send_json(_instance_index(root, default_instance), send_body=send_body)
                return
            if path == "/api/timeline":
                try:
                    timeline_path = _resolve_timeline_path(root, default_instance, query.get("path", [None])[0])
                    self._send_json(TimelineDataLoader(timeline_path).load(), send_body=send_body)
                except ValueError as error:
                    self.send_error(HTTPStatus.BAD_REQUEST, str(error))
                return
            if static_root is not None:
                if self._send_static(path, static_root, send_body=send_body):
                    return
            if path == "/":
                self._send_text(HTML, "text/html; charset=utf-8", send_body=send_body)
                return
            if path == "/style.css":
                self._send_text(CSS, "text/css; charset=utf-8", send_body=send_body)
                return
            if path == "/app.js":
                self._send_text(JS, "application/javascript; charset=utf-8", send_body=send_body)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "not found")

        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_text(self, body: str, content_type: str, *, send_body: bool) -> None:
            encoded = body.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if send_body:
                self.wfile.write(encoded)

        def _send_json(self, body: object, *, send_body: bool) -> None:
            encoded = json.dumps(_jsonable(body), ensure_ascii=False, sort_keys=True).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if send_body:
                self.wfile.write(encoded)

        def _send_static(self, path: str, root: Path, *, send_body: bool) -> bool:
            relative = "index.html" if path in {"", "/"} else path.removeprefix("/")
            candidate = (root / relative).resolve()
            if not _is_relative_to(candidate, root):
                self.send_error(HTTPStatus.FORBIDDEN, "forbidden")
                return True
            if not candidate.exists() or not candidate.is_file():
                candidate = root / "index.html"
            if not candidate.exists() or not candidate.is_file():
                return False
            content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            payload = candidate.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if send_body:
                self.wfile.write(payload)
            return True

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


def _valid_static_root(path: Path | None) -> Path | None:
    if path is None:
        return None
    root = path.expanduser().resolve()
    return root if (root / "index.html").exists() else None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _default_instance(
    root: Path,
    *,
    selected_instance_path: Path | None,
    mode: str | None,
    run_id: str | None,
) -> Path | None:
    if selected_instance_path is not None:
        return selected_instance_path.expanduser().resolve()
    try:
        return find_latest_instance(root, mode=mode, run_id=run_id)
    except ValueError:
        return None


def _instance_index(root: Path, default_instance: Path | None) -> dict[str, object]:
    rows = list_instances(root)
    return {
        "root": str(root),
        "defaultPath": str(default_instance) if default_instance is not None else None,
        "instances": rows,
        "count": len(rows),
    }


def _resolve_timeline_path(root: Path, default_instance: Path | None, raw_path: str | None) -> Path:
    if not raw_path:
        raise ValueError("timeline path is required")
    candidate = Path(raw_path).expanduser().resolve()
    if not _is_relative_to(candidate, root):
        raise ValueError(f"run instance is outside run root: {candidate}")
    return candidate


def _viewer_url(host: str, port: int, default_instance: Path | None) -> str:
    base = f"http://{host}:{port}"
    if default_instance is None:
        return base
    return f"{base}?{urlencode({'path': str(default_instance)})}"


def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
