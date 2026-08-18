from __future__ import annotations

import asyncio
import json
from pathlib import Path
from datetime import datetime
from typing import Any

from aiohttp import web

from ..domain.lifecycle import StrategyLifecycle
from kairospy.strategy import StrategyCommand
from ..application.runtime import StrategyApplication, StrategyStatus


class StrategyControlServer:
    """Small HTTP/1.1 control plane over an instance-owned Unix socket."""

    def __init__(
        self, application: StrategyApplication, socket_path: str | Path
    ) -> None:
        self.application = application
        self.socket_path = Path(socket_path)
        self._server: asyncio.AbstractServer | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.UnixSite | None = None
        self._event_task: asyncio.Task[None] | None = None
        self._command_tasks: dict[str, asyncio.Task[object]] = {}
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        self._stopped.clear()
        self.socket_path.unlink(missing_ok=True)
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        application = web.Application(client_max_size=1024 * 1024)
        application.router.add_route("*", "/{path_info:.*}", self._handle)
        # This server listens on AF_UNIX, where SO_KEEPALIVE is not a valid
        # socket option.  aiohttp otherwise applies TCP keepalive to every
        # accepted transport, which raises EINVAL on macOS.
        self._runner = web.AppRunner(application, tcp_keepalive=False)
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
        self.application.close()
        if self._event_task is not None and not self._event_task.done():
            self._event_task.cancel()
            try:
                await self._event_task
            except asyncio.CancelledError:
                pass
        self._event_task = None
        for task in tuple(self._command_tasks.values()):
            task.cancel()
        self._command_tasks.clear()
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
            result = await self._dispatch(request.method, target, body)
            return web.json_response(result)
        except Exception as error:
            return web.json_response({"error": str(error)}, status=400)

    async def _dispatch(self, method: str, path: str, body: bytes) -> dict[str, Any]:
        if method == "GET" and path == "/v1/health":
            status = self.application.status
            return self._status(status) | {
                "status": "ready"
                if status.state
                not in {StrategyLifecycle.FAILED, StrategyLifecycle.STOPPED}
                else "not_ready"
            }
        if method == "GET" and path.startswith("/v1/decisions/"):
            decision_id = path.removeprefix("/v1/decisions/").strip()
            if not decision_id:
                raise ValueError("strategy_decision_id is required")
            trace = self.application.decision_trace(decision_id)
            if trace is None:
                raise ValueError(f"Strategy decision not found: {decision_id}")
            return trace
        if method == "GET":
            raise ValueError(
                "unsupported Strategy query; use /v1/health or /v1/decisions/{id}"
            )
        if method == "POST" and path == "/v1/command":
            payload = json.loads(body or b"{}")
            if not isinstance(payload, dict):
                raise ValueError("command body must be an object")
            command = StrategyCommand(
                request_id=str(payload.get("request_id") or "").strip(),
                kind=str(payload.get("kind") or "").strip(),
                source=str(payload.get("source") or ""),
                payload=(
                    payload.get("payload")
                    if isinstance(payload.get("payload"), dict)
                    else {}
                ),
            )
            task = asyncio.create_task(self.application.command(command))
            self._command_tasks[command.request_id] = task
            try:
                result = await task
            finally:
                self._command_tasks.pop(command.request_id, None)
            return {
                "request_id": result.request_id,
                "status": result.status,
                "result": dict(result.result),
                "error": result.error,
                "error_code": result.error_code,
                "retryable": result.retryable,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        if method == "POST" and path == "/v1/command/cancel":
            payload = json.loads(body or b"{}")
            request_id = str(payload.get("request_id") or "").strip()
            if not request_id:
                raise ValueError("command cancel requires request_id")
            task = self._command_tasks.get(request_id)
            if task is None:
                return {"request_id": request_id, "status": "not_found"}
            task.cancel()
            return {"request_id": request_id, "status": "cancel_requested"}
        if method == "POST" and path == "/v1/start":
            return self._status(self.application.start())
        if method == "POST" and path == "/v1/enable":
            result = self.application.enable()
            self._event_task = asyncio.create_task(self.application.run())
            self._event_task.add_done_callback(self._event_task_finished)
            return self._status(result)
        if method == "POST" and path == "/v1/pause":
            return self._status(self.application.pause())
        if method == "POST" and path == "/v1/resume":
            return self._status(self.application.resume())
        if method == "POST" and path == "/v1/refresh":
            return self._status(self.application.refresh())
        if method == "POST" and path == "/v1/stop":
            result = self.application.stop()
            if self._event_task is not None and not self._event_task.done():
                self._event_task.cancel()
            self._stopped.set()
            return self._status(result)
        raise ValueError(f"unsupported strategy control request: {method} {path}")

    def _event_task_finished(self, task: asyncio.Task[None]) -> None:
        """Wake the process lifecycle when a finite replay reaches EOF."""
        if not task.cancelled():
            self._stopped.set()

    def _status(self, status: StrategyStatus) -> dict[str, Any]:
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
            "dispatch_sequence": status.dispatch_sequence,
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
            "equity_curve": list(self.application.equity_curve),
            "notifications": self.application.context.notifications.health(),
            "decisions": self.application.decisions.health(),
            "decision_traces": self.application.decisions.traces(),
            "execution_events": self.application.context.execution.health(),
        }
