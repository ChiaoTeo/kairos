from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime
from typing import Any

from aiohttp import web

from ..domain.lifecycle import StrategyLifecycle
from .host import StrategyHost, StrategyHostStatus


class StrategyControlServer:
    """Small HTTP/1.1 control plane over an instance-owned Unix socket."""

    def __init__(self, host: StrategyHost, socket_path: str | Path) -> None:
        self.host = host
        self.socket_path = Path(socket_path)
        self._server: asyncio.AbstractServer | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.UnixSite | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        self._stopped.clear()
        self.socket_path.unlink(missing_ok=True)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        application = web.Application(client_max_size=1024 * 1024)
        application.router.add_route("*", "/{path_info:.*}", self._handle)
        self._runner = web.AppRunner(application)
        await self._runner.setup()
        self._site = web.UnixSite(self._runner, str(self.socket_path))
        await self._site.start()

    async def serve_until_stopped(self) -> None:
        stop_task = asyncio.create_task(self._stopped.wait())
        event_task = self._event_task
        if event_task is None:
            await stop_task
            return
        done, pending = await asyncio.wait(
            {stop_task, event_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if event_task in done and not self._stopped.is_set():
            error = event_task.exception()
            if error is not None:
                raise error
            self._stopped.set()

    async def close(self) -> None:
        if self._event_task is not None and not self._event_task.done():
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        self._event_task = None
        self._stopped.set()
        if self._runner is None:
            return
        await self._runner.cleanup()
        self._runner = None
        self.socket_path.unlink(missing_ok=True)

    async def _handle(self, request: web.Request) -> web.Response:
        try:
            body = await request.read()
            target = request.path
            if request.query_string:
                target += f"?{request.query_string}"
            result = self._dispatch(request.method, target, body)
            return web.json_response(result)
        except Exception as error:
            return web.json_response({"error": str(error)}, status=400)

    def _dispatch(self, method: str, path: str, body: bytes) -> dict[str, Any]:
        if method == "GET" and path == "/v1/health":
            status = self.host.status
            return self._status(status) | {
                "status": "ready"
                if status.state
                not in {StrategyLifecycle.FAILED, StrategyLifecycle.STOPPED}
                else "not_ready"
            }
        if method == "GET" and path == "/v1/status":
            return self._status(self.host.status)
        if method == "POST" and path == "/v1/start":
            return self._status(self.host.start())
        if method == "POST" and path == "/v1/enable":
            result = self.host.enable()
            self._event_task = asyncio.create_task(self.host.run())
            return self._status(result)
        if method == "POST" and path == "/v1/pause":
            return self._status(self.host.pause())
        if method == "POST" and path == "/v1/resume":
            return self._status(self.host.resume())
        if method == "POST" and path == "/v1/refresh":
            return self._status(self.host.refresh())
        if method == "POST" and path == "/v1/stop":
            result = self.host.stop()
            if self._event_task is not None and not self._event_task.done():
                self._event_task.cancel()
            self._stopped.set()
            return self._status(result)
        raise ValueError(f"unsupported strategy control request: {method} {path}")

    def _status(self, status: StrategyHostStatus) -> dict[str, Any]:
        last_event_time = (
            status.last_event_time.isoformat() if status.last_event_time else None
        )
        last_event_age_ms = None
        if status.last_event_time is not None:
            last_event_age_ms = max(
                0,
                int(
                    (
                        datetime.now(status.last_event_time.tzinfo)
                        - status.last_event_time
                    ).total_seconds()
                    * 1000
                ),
            )
        return {
            "status": getattr(status.state, "value", str(status.state)),
            "launch_id": status.launch_id,
            "instance_id": status.instance_id,
            "strategy_id": status.strategy_id,
            "event_sequence": status.event_sequence,
            "reason": status.reason,
            "readiness": status.readiness.value,
            "data_health": status.data_health.value,
            "subscription_count": status.subscription_count,
            "active_subscription_count": status.active_subscription_count,
            "first_event_received": status.first_event_received,
            "last_event_time": status.last_event_time.isoformat()
            if status.last_event_time
            else None,
            "last_event_age_ms": last_event_age_ms,
            "last_event_kind": status.last_event_kind,
            "event_count": status.event_count,
            "subscriptions": [
                dict(subscription) for subscription in status.subscriptions
            ],
            "equity_curve": list(self.host.equity_curve),
        }
