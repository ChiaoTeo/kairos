from __future__ import annotations

import asyncio

from .runtime import RuntimeStatus, TradingApplication
from .task_supervisor import AsyncTaskSupervisor, ManagedTaskSnapshot, ManagedTaskSpec, TaskCriticality, TaskFault


class AsyncTradingRuntime:
    """Bind durable TradingApplication gates to supervised asynchronous services."""

    def __init__(self, application: TradingApplication, tasks: tuple[ManagedTaskSpec, ...], *,
                 supervisor: AsyncTaskSupervisor | None = None) -> None:
        if not tasks:
            raise ValueError("async trading runtime requires at least one managed task")
        self.application = application
        self.tasks = tasks
        self.supervisor = supervisor or AsyncTaskSupervisor()
        self.started = False

    async def start(self) -> None:
        if self.started:
            raise RuntimeError("async trading runtime is already started")
        self.application.start()
        try:
            await self.supervisor.start(self.tasks)
            await asyncio.sleep(0)
            failed = tuple(item for item in self.supervisor.snapshots()
                           if item.criticality is TaskCriticality.CRITICAL and item.status.value == "failed")
            if failed:
                raise RuntimeError("critical managed task failed during startup: " + ",".join(item.name for item in failed))
            self.application.run()
            self.started = True
        except Exception:
            await self.supervisor.stop()
            self.application.stop()
            raise

    async def wait_for_critical_fault(self) -> TaskFault:
        if not self.started:
            raise RuntimeError("async trading runtime is not started")
        fault = await self.supervisor.wait_critical_fault()
        if self.application.status in {RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.REDUCE_ONLY}:
            self.application.degrade(
                f"managed task {fault.task_name} failed: {fault.error_type}: {fault.message}",
                reduce_only=True,
            )
        return fault

    async def stop(self, *, timeout_seconds: float = 5.0) -> None:
        if not self.started:
            return
        try:
            await self.supervisor.stop(timeout_seconds=timeout_seconds)
        finally:
            self.application.stop()
            self.started = False

    def task_snapshots(self) -> tuple[ManagedTaskSnapshot, ...]:
        return self.supervisor.snapshots()
