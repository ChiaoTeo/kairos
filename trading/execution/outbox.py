from __future__ import annotations

import asyncio
from typing import Callable, TYPE_CHECKING

from trading.adapters.base import ComboOrderRequest, OrderRequest
from trading.application.clock import Clock, SystemClock
from trading.execution.router import ExecutionRouter
from trading.orchestration.runtime_store import SQLiteRuntimeStore
from trading.orchestration.kill_switch import KillSwitch

from .command import OutboxRecord

if TYPE_CHECKING:
    from trading.application.runtime import TradingApplication


class DurableOrderCommandService:
    """Safety-gated application port that accepts already planned order commands."""

    def __init__(
        self,
        store: SQLiteRuntimeStore,
        application: "TradingApplication",
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
        self.validate(request)
        return self.store.enqueue_order_command(request, self.clock.now())


class DurableOrderDispatcher:
    """Dispatch locally durable commands without treating transport return as creation."""

    def __init__(self, store: SQLiteRuntimeStore, router: ExecutionRouter, *, clock: Clock | None = None) -> None:
        self.store = store
        self.router = router
        self.clock = clock or SystemClock()

    def enqueue(self, request) -> OutboxRecord:
        return self.store.enqueue_order_command(request, self.clock.now())

    async def dispatch_once(self) -> bool:
        at = self.clock.now()
        record = self.store.claim_next_order_command(at)
        if record is None:
            return False
        request = record.command.request
        submit = self.router.submit_combo if isinstance(request, ComboOrderRequest) else self.router.submit
        try:
            ack = await asyncio.to_thread(submit, request, at)
        except ValueError as error:
            self.store.fail_order_command(record.command.command_id, str(error), self.clock.now(), terminal=True)
            raise
        except Exception as error:
            self.store.fail_order_command(record.command.command_id, str(error), self.clock.now(), terminal=False)
            raise
        self.store.complete_order_command(record.command.command_id, ack, ack.accepted_at)
        return True

    async def run(self, *, idle_wait_seconds: float = 0.05) -> None:
        if idle_wait_seconds <= 0:
            raise ValueError("outbox idle wait must be positive")
        while True:
            dispatched = await self.dispatch_once()
            if not dispatched:
                await asyncio.sleep(idle_wait_seconds)
