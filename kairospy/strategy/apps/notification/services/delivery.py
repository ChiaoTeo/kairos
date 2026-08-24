from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import TYPE_CHECKING, Mapping

from kairospy.infrastructure.observability import (
    record_counter,
    record_duration_ms,
    record_gauge,
)

if TYPE_CHECKING:
    from kairospy.strategy.api.logging import StrategyLogger

from ..application.models import (
    NotificationDestination,
    NotificationReceipt,
    NotificationRequest,
    RenderedNotification,
    SenderResult,
)
from .senders import NotificationSender


@dataclass(slots=True)
class _DestinationHealth:
    sender: str
    credential_id: str | None = None
    state: str = "healthy"
    consecutive_failures: int = 0
    delivered_total: int = 0
    failed_total: int = 0
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error_code: str | None = None


@dataclass(slots=True)
class _Counters:
    accepted_total: int = 0
    duplicate_total: int = 0
    rejected_total: int = 0
    delivered_total: int = 0
    failed_total: int = 0


@dataclass(frozen=True, slots=True)
class _QueuedNotification:
    message: RenderedNotification
    destinations: tuple[str, ...]


class NotificationDeliveryRuntime:
    """One instance-owned queue, dedupe cache, sender loop and health owner."""

    def __init__(
        self,
        *,
        identity: Mapping[str, str],
        routes: Mapping[str, tuple[str, ...]],
        default_routes: tuple[str, ...],
        destinations: Mapping[str, NotificationDestination],
        senders: Mapping[str, NotificationSender],
        queue_capacity: int = 256,
        dedupe_ttl_seconds: float = 3600,
        dedupe_capacity: int = 4096,
        shutdown_grace_seconds: float = 5,
        logger: "StrategyLogger | None" = None,
        enabled: bool = True,
        initial_state: str = "healthy",
        journal_path: Path | None = None,
        health_path: Path | None = None,
        config_hash: str | None = None,
        config_issues: tuple[str, ...] = (),
    ) -> None:
        self.identity = dict(identity)
        self.routes = {key: tuple(value) for key, value in routes.items()}
        self.default_routes = tuple(default_routes)
        self.destinations = dict(destinations)
        self.senders = dict(senders)
        self.queue: asyncio.Queue[_QueuedNotification] = asyncio.Queue(
            maxsize=queue_capacity
        )
        self.dedupe_ttl_seconds = dedupe_ttl_seconds
        self.dedupe_capacity = dedupe_capacity
        self.shutdown_grace_seconds = shutdown_grace_seconds
        self.logger = logger
        self.enabled = enabled
        self.journal_path = journal_path
        self.health_path = health_path
        self._accepting = enabled
        self._state = "disabled" if not enabled else initial_state
        self._sequence = 0
        self._dedupe: OrderedDict[str, float] = OrderedDict()
        self._durable_dedupe = self._load_durable_dedupe()
        self._counters = _Counters()
        self._destination_health = {
            destination_id: _DestinationHealth(
                destination.sender, destination.credential_id
            )
            for destination_id, destination in self.destinations.items()
        }
        self._task: asyncio.Task[None] | None = None
        self._last_delivery_at: str | None = None
        self._last_failure_at: str | None = None
        self._inflight_destinations = 0
        self.config_hash = config_hash
        self.config_issues = tuple(config_issues)

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(), name=f"notification:{self.identity.get('instance_id', '')}"
        )
        self._write_health()

    def submit(self, request: NotificationRequest) -> NotificationReceipt:
        routes = request.routes or self.default_routes
        if not self.enabled:
            return self._reject("notifications_disabled", routes)
        if not self._accepting:
            return self._reject("runtime_stopping", routes)
        if self._task is not None and self._task.done():
            self._state = "unhealthy"
            return self._reject("runtime_unhealthy", routes)
        if not routes:
            return self._reject("route_has_no_destination", routes)
        unknown = tuple(route for route in routes if route not in self.routes)
        if unknown:
            return self._reject(f"unknown_route:{','.join(unknown)}", routes)
        destination_ids = tuple(
            dict.fromkeys(
                destination_id
                for route in routes
                for destination_id in self.routes.get(route, ())
            )
        )
        if not destination_ids:
            return self._reject("route_has_no_destination", routes)
        now = time.monotonic()
        self._prune_dedupe(now)
        dedupe_key_sha256: str | None = None
        if request.dedupe_key is not None:
            key = self._scoped_dedupe_key(request.dedupe_key)
            dedupe_key_sha256 = hashlib.sha256(key.encode("utf-8")).hexdigest()
            if key in self._dedupe or dedupe_key_sha256 in self._durable_dedupe:
                if key in self._dedupe:
                    self._dedupe.move_to_end(key)
                self._counters.duplicate_total += 1
                record_counter("kairos.notification.publish.duplicate")
                self._write_health()
                return NotificationReceipt("", "duplicate", routes, 0, "duplicate")
            self._dedupe[key] = now
            while len(self._dedupe) > self.dedupe_capacity:
                self._dedupe.popitem(last=False)
        self._sequence += 1
        notification_id = self._notification_id(request, self._sequence)
        occurred_at = request.occurred_at or datetime.now(timezone.utc)
        message = RenderedNotification(
            notification_id=notification_id,
            title=request.title,
            body=request.body,
            severity=request.severity,
            occurred_at=occurred_at.astimezone(timezone.utc),
            attributes=request.attributes,
            identity=self.identity,
            dedupe_key_sha256=dedupe_key_sha256,
        )
        try:
            self.queue.put_nowait(_QueuedNotification(message, destination_ids))
        except asyncio.QueueFull:
            if request.dedupe_key is not None:
                self._dedupe.pop(self._scoped_dedupe_key(request.dedupe_key), None)
            return self._reject("queue_full", routes, notification_id)
        self._counters.accepted_total += 1
        record_counter("kairos.notification.publish.accepted")
        record_gauge("kairos.notification.queue.depth", self.queue.qsize())
        self._write_health()
        return NotificationReceipt(
            notification_id,
            "accepted",
            routes,
            len(destination_ids),
        )

    def reject_invalid(
        self, routes: tuple[str, ...], detail: str
    ) -> NotificationReceipt:
        self._log(
            "warning",
            "invalid notification request",
            error_code="invalid_request",
        )
        del detail
        return self._reject("invalid_request", routes)

    async def flush(self, timeout: float | None = None) -> bool:
        if not self.enabled or self._task is None:
            return True
        try:
            await asyncio.wait_for(
                self.queue.join(),
                timeout=self.shutdown_grace_seconds if timeout is None else timeout,
            )
            return True
        except asyncio.TimeoutError:
            return False

    async def close(self) -> None:
        self._accepting = False
        if not self.enabled:
            return
        self._state = "stopping"
        drained = await self.flush()
        undelivered = (
            self.queue.qsize() + self._inflight_destinations if not drained else 0
        )
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if not drained:
            self._counters.failed_total += undelivered
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                    self.queue.task_done()
                except asyncio.QueueEmpty:
                    break
            record_gauge("kairos.notification.queue.depth", self.queue.qsize())
        self._state = "stopped" if drained else "unhealthy"
        self._write_health()

    def health(self) -> dict[str, object]:
        return {
            "state": self._state,
            "queue_depth": self.queue.qsize(),
            "queue_capacity": self.queue.maxsize,
            "inflight_destinations": self._inflight_destinations,
            "accepted_total": self._counters.accepted_total,
            "duplicate_total": self._counters.duplicate_total,
            "rejected_total": self._counters.rejected_total,
            "delivered_total": self._counters.delivered_total,
            "failed_total": self._counters.failed_total,
            "last_delivery_at": self._last_delivery_at,
            "last_failure_at": self._last_failure_at,
            "config_schema_version": 1,
            "config_hash": self.config_hash,
            "config_issues": list(self.config_issues),
            "destinations": {
                destination_id: {
                    "sender": value.sender,
                    "credential_id": value.credential_id,
                    "state": value.state,
                    "consecutive_failures": value.consecutive_failures,
                    "delivered_total": value.delivered_total,
                    "failed_total": value.failed_total,
                    "last_success_at": value.last_success_at,
                    "last_failure_at": value.last_failure_at,
                    "last_error_code": value.last_error_code,
                }
                for destination_id, value in self._destination_health.items()
            },
        }

    def delivery_records(
        self, notification_ids: tuple[str, ...]
    ) -> tuple[dict[str, object], ...]:
        """Read redacted destination results for diagnostic aggregation."""

        wanted = {value for value in notification_ids if value}
        if not wanted or self.journal_path is None:
            return ()
        paths = (
            self.journal_path.with_suffix(self.journal_path.suffix + ".1"),
            self.journal_path,
        )
        records: list[dict[str, object]] = []
        for path in paths:
            if not path.is_file():
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"notification journal line {line_number} is invalid: {path}"
                    ) from error
                if isinstance(value, dict) and value.get("notification_id") in wanted:
                    records.append(value)
        return tuple(records)

    async def _run(self) -> None:
        try:
            while True:
                queued = await self.queue.get()
                try:
                    self._inflight_destinations = len(queued.destinations)
                    await asyncio.gather(
                        *(
                            self._deliver(destination_id, queued.message)
                            for destination_id in queued.destinations
                        )
                    )
                finally:
                    self._inflight_destinations = 0
                    self.queue.task_done()
                    record_gauge("kairos.notification.queue.depth", self.queue.qsize())
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._state = "unhealthy"
            self._log(
                "error",
                "notification delivery worker failed",
                error_code=type(error).__name__,
            )
            self._write_health()

    async def _deliver(
        self, destination_id: str, message: RenderedNotification
    ) -> None:
        destination = self.destinations[destination_id]
        sender = self.senders[destination_id]
        started = time.monotonic()
        try:
            result = await sender.send(destination, message)
        except Exception as error:
            result = SenderResult("failed", type(error).__name__)
        record_counter("kairos.notification.delivery.attempts")
        record_duration_ms(
            "kairos.notification.delivery.latency",
            (time.monotonic() - started) * 1000,
        )
        self._log(
            "info" if result.delivered else "warning",
            "notification delivery attempt",
            notification_id=message.notification_id,
            destination_id=destination_id,
            sender=destination.sender,
            outcome=result.outcome,
            error_code=result.error_code,
        )
        self._record_result(destination_id, result)
        self._write_journal(destination_id, message, result)
        self._write_health()

    def _record_result(self, destination_id: str, result: SenderResult) -> None:
        now = datetime.now(timezone.utc).isoformat()
        health = self._destination_health[destination_id]
        if result.delivered:
            health.state = "healthy"
            health.consecutive_failures = 0
            health.delivered_total += 1
            health.last_success_at = now
            health.last_error_code = None
            self._counters.delivered_total += 1
            record_counter("kairos.notification.delivery.success")
            self._last_delivery_at = now
            if not self.config_issues and all(
                value.state == "healthy" for value in self._destination_health.values()
            ):
                self._state = "healthy"
            return
        health.state = "degraded"
        health.consecutive_failures += 1
        health.failed_total += 1
        health.last_failure_at = now
        health.last_error_code = result.error_code
        self._counters.failed_total += 1
        record_counter("kairos.notification.delivery.failures")
        self._last_failure_at = now
        self._state = "degraded"

    def _reject(
        self,
        reason: str,
        routes: tuple[str, ...],
        notification_id: str = "",
    ) -> NotificationReceipt:
        self._counters.rejected_total += 1
        record_counter("kairos.notification.publish.rejected")
        self._log("warning", "notification publish rejected", reason=reason)
        self._write_health()
        return NotificationReceipt(notification_id, "rejected", routes, 0, reason)

    def _prune_dedupe(self, now: float) -> None:
        cutoff = now - self.dedupe_ttl_seconds
        while self._dedupe:
            _, inserted = next(iter(self._dedupe.items()))
            if inserted >= cutoff:
                break
            self._dedupe.popitem(last=False)

    def _scoped_dedupe_key(self, key: str) -> str:
        prefix = ":".join(
            self.identity.get(name, "")
            for name in ("workspace_id", "launch_id", "instance_id", "strategy_id")
        )
        return f"{prefix}:{key}"

    def _notification_id(self, request: NotificationRequest, sequence: int) -> str:
        payload = "\0".join(
            (
                self._scoped_dedupe_key(str(sequence)),
                request.title,
                request.body,
                request.severity,
                request.dedupe_key or "",
                request.occurred_at.isoformat() if request.occurred_at else "",
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _log(self, level: str, message: str, **data: object) -> None:
        if self.logger is None:
            return
        getattr(self.logger, level)(message, event="notification_delivery", **data)

    def _write_health(self) -> None:
        if self.health_path is None:
            return
        try:
            self.health_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.health_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.health(), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.health_path)
        except OSError as error:
            self._log(
                "warning",
                "notification health write failed",
                error_code=type(error).__name__,
            )

    def _write_journal(
        self,
        destination_id: str,
        message: RenderedNotification,
        result: SenderResult,
    ) -> None:
        if self.journal_path is None:
            return
        try:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            if (
                self.journal_path.is_file()
                and self.journal_path.stat().st_size > 1_000_000
            ):
                rotated = self.journal_path.with_suffix(self.journal_path.suffix + ".1")
                rotated.unlink(missing_ok=True)
                self.journal_path.replace(rotated)
            record = {
                "schema_version": 1,
                "notification_id": message.notification_id,
                "destination_id": destination_id,
                "title": message.title,
                "body_sha256": hashlib.sha256(message.body.encode("utf-8")).hexdigest(),
                "body_bytes": len(message.body.encode("utf-8")),
                "severity": message.severity,
                "dedupe_key_sha256": message.dedupe_key_sha256,
                "occurred_at": message.occurred_at.isoformat(),
                "outcome": result.outcome,
                "error_code": result.error_code,
            }
            with self.journal_path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                )
            if message.dedupe_key_sha256 is not None:
                self._durable_dedupe.add(message.dedupe_key_sha256)
        except OSError as error:
            self._log(
                "warning",
                "notification journal write failed",
                error_code=type(error).__name__,
            )

    def _load_durable_dedupe(self) -> set[str]:
        if self.journal_path is None:
            return set()
        values: set[str] = set()
        paths = (
            self.journal_path.with_suffix(self.journal_path.suffix + ".1"),
            self.journal_path,
        )
        for path in paths:
            if not path.is_file():
                continue
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                value = (
                    record.get("dedupe_key_sha256")
                    if isinstance(record, dict)
                    else None
                )
                if isinstance(value, str) and value:
                    values.add(value)
        return values


__all__ = ["NotificationDeliveryRuntime"]
