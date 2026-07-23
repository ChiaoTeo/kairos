from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable, TYPE_CHECKING

from kairospy.execution.ports import ComboOrderRequest, OrderRequest
from kairospy.runtime.clock import Clock, SystemClock
from kairospy.execution.router import ExecutionRouter
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore

from .command import OutboxRecord, OutboxStatus

if TYPE_CHECKING:
    from kairospy.governance.kill_switch import KillSwitch
    from kairospy.runtime.application import KairosApplication


class DurableOrderCommandService:
    """Safety-gated application port that accepts already planned order commands."""

    def __init__(
        self,
        store: SQLiteRuntimeStore,
        application: "KairosApplication",
        kill_switch: KillSwitch,
        validate: Callable[[OrderRequest | ComboOrderRequest], None],
        *,
        clock: Clock | None = None,
    ) -> None:
        self.store = store
        self.application = application
        self.kill_switch = kill_switch
        self.validate = validate
        self.clock = clock or SystemClock()

    def submit(self, request: OrderRequest | ComboOrderRequest) -> OutboxRecord:
        self.application.require_operational()
        if self.application.status.value == "reduce_only" and not request.instructions.reduce_only:
            raise RuntimeError("runtime is reduce-only: non-reducing commands are blocked")
        if self.kill_switch.triggered and not request.instructions.reduce_only:
            raise RuntimeError("kill switch active: non-reducing commands are blocked")
        if not request.instructions.reduce_only:
            reconciliation = self.store.runtime_state("reconciliation:last")
            if isinstance(reconciliation, dict) and reconciliation.get("matched") is False:
                raise RuntimeError("reconciliation mismatch: non-reducing commands are blocked")
            risk_state = self.store.runtime_state("risk_runtime:last")
            if isinstance(risk_state, dict) and str(risk_state.get("status") or "ok") not in {"ok", "ready"}:
                raise RuntimeError("risk runtime state blocks non-reducing commands")
        self.validate(request)
        return self.store.enqueue_order_command(request, self.clock.now())


class DurableOrderDispatcher:
    """Dispatch locally durable commands without treating transport return as creation."""

    def __init__(self, store: SQLiteRuntimeStore, router: ExecutionRouter, *, clock: Clock | None = None) -> None:
        self.store = store
        self.router = router
        self.clock = clock or SystemClock()
        self.last_metrics: dict[str, object] = {}

    def enqueue(self, request) -> OutboxRecord:
        return self.store.enqueue_order_command(request, self.clock.now())

    async def dispatch_once(self) -> bool:
        at = self.clock.now()
        record = self.store.claim_next_order_command(at)
        if record is None:
            self.last_metrics = self._metrics(None, at)
            return False
        request = record.command.request
        submit = self.router.submit_combo if isinstance(request, ComboOrderRequest) else self.router.submit
        try:
            ack = await asyncio.to_thread(submit, request, at)
        except ValueError as error:
            self.store.fail_order_command(record.command.command_id, str(error), self.clock.now(), terminal=True)
            self.last_metrics = self._metrics(record, self.clock.now())
            raise
        except Exception as error:
            self.store.fail_order_command(record.command.command_id, str(error), self.clock.now(), terminal=False)
            self.last_metrics = self._metrics(record, self.clock.now())
            raise
        self.store.complete_order_command(record.command.command_id, ack, ack.accepted_at)
        self.last_metrics = self._metrics(record, self.clock.now(), ack_accepted_at=ack.accepted_at)
        return True

    async def run(self, *, idle_wait_seconds: float = 0.05, after_dispatch: Callable[[], None] | None = None) -> None:
        if idle_wait_seconds <= 0:
            raise ValueError("outbox idle wait must be positive")
        while True:
            dispatched = await self.dispatch_once()
            if after_dispatch is not None:
                after_dispatch()
            if not dispatched:
                await asyncio.sleep(idle_wait_seconds)

    def _metrics(
        self,
        record: OutboxRecord | None,
        observed_at: datetime,
        *,
        ack_accepted_at: datetime | None = None,
    ) -> dict[str, object]:
        pending = self.store.outbox_commands(OutboxStatus.PENDING)
        dispatching = self.store.outbox_commands(OutboxStatus.DISPATCHING)
        unknown = self.store.outbox_commands(OutboxStatus.UNKNOWN)
        metrics: dict[str, object] = {
            "outbox_pending_count": len(pending),
            "outbox_dispatching_count": len(dispatching),
            "outbox_unknown_count": len(unknown),
            "outbox_backlog_count": len(pending) + len(dispatching) + len(unknown),
            "observed_at": observed_at.isoformat(),
        }
        if record is None:
            for key in (
                "last_command_id",
                "last_client_order_id",
                "order_submit_latency_last_ms",
                "order_ack_latency_last_ms",
            ):
                if key in self.last_metrics:
                    metrics[key] = self.last_metrics[key]
            return metrics
        if record is not None:
            submit_latency_ms = _milliseconds(observed_at - record.command.created_at)
            metrics.update({
                "last_command_id": record.command.command_id,
                "last_client_order_id": record.command.request.client_order_id,
                "order_submit_latency_last_ms": submit_latency_ms,
            })
            if ack_accepted_at is not None:
                metrics["order_ack_latency_last_ms"] = _milliseconds(ack_accepted_at - record.updated_at)
        return metrics


class DurableOrderDispatcherService:
    """Managed service wrapper for continuously draining the durable order outbox."""

    STATE_KEY_PREFIX = "order_outbox_dispatcher"

    def __init__(
        self,
        store: SQLiteRuntimeStore,
        dispatcher: DurableOrderDispatcher,
        *,
        run_id: str,
        idle_wait_seconds: float = 0.05,
        clock: Clock | None = None,
    ) -> None:
        if not str(run_id).strip():
            raise ValueError("outbox dispatcher service requires run_id")
        if idle_wait_seconds <= 0:
            raise ValueError("outbox idle wait must be positive")
        self.store = store
        self.dispatcher = dispatcher
        self.run_id = str(run_id)
        self.idle_wait_seconds = idle_wait_seconds
        self.clock = clock or SystemClock()

    @property
    def state_key(self) -> str:
        return f"{self.STATE_KEY_PREFIX}:{self.run_id}"

    def managed_service(self, name: str | None = None):
        from kairospy.runtime.service_supervisor import ManagedServiceSpec

        return ManagedServiceSpec(name or f"outbox-dispatcher:{self.run_id}", self.run)

    async def run(self) -> None:
        self._persist("running", {"reason": "started"})
        try:
            await self.dispatcher.run(
                idle_wait_seconds=self.idle_wait_seconds,
                after_dispatch=lambda: self._persist("running", {"reason": "drain cycle"}),
            )
        except asyncio.CancelledError:
            self._persist("stopped", {"reason": "service stopped"})
            raise
        except Exception as error:
            self._persist("failed", {
                "error_type": type(error).__name__,
                "message": str(error),
            })
            raise

    def _persist(self, phase: str, evidence: dict[str, object]) -> None:
        metrics = dict(self.dispatcher.last_metrics)
        previous = self.store.runtime_state(self.state_key)
        if isinstance(previous, dict):
            previous_max_submit = previous.get("order_submit_latency_max_ms")
            previous_max_ack = previous.get("order_ack_latency_max_ms")
            if previous_max_submit is not None and metrics.get("order_submit_latency_last_ms") is not None:
                metrics["order_submit_latency_max_ms"] = max(
                    float(previous_max_submit),
                    float(metrics["order_submit_latency_last_ms"]),
                )
            if previous_max_ack is not None and metrics.get("order_ack_latency_last_ms") is not None:
                metrics["order_ack_latency_max_ms"] = max(
                    float(previous_max_ack),
                    float(metrics["order_ack_latency_last_ms"]),
                )
        if metrics.get("order_submit_latency_last_ms") is not None and metrics.get("order_submit_latency_max_ms") is None:
            metrics["order_submit_latency_max_ms"] = metrics["order_submit_latency_last_ms"]
        if metrics.get("order_ack_latency_last_ms") is not None and metrics.get("order_ack_latency_max_ms") is None:
            metrics["order_ack_latency_max_ms"] = metrics["order_ack_latency_last_ms"]
        self.store.set_runtime_state(
            self.state_key,
            {
                "run_id": self.run_id,
                "phase": phase,
                "updated_at": self.clock.now(),
                **metrics,
                **evidence,
            },
            self.clock.now(),
        )


def _milliseconds(delta) -> float:
    return max(0.0, delta.total_seconds() * 1000.0)
