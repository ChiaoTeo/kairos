from __future__ import annotations

import asyncio

from .application import RuntimeStatus, KairosApplication
from kairospy.runtime.service_supervisor import (
    AsyncServiceSupervisor,
    ManagedServiceSnapshot,
    ManagedServiceSpec,
    ServiceCriticality,
    ServiceFault,
)


class AsyncKairosRuntime:
    """Bind durable KairosApplication gates to supervised asynchronous services."""

    def __init__(self, application: KairosApplication, tasks: tuple[ManagedServiceSpec, ...], *,
                 supervisor: AsyncServiceSupervisor | None = None) -> None:
        if not tasks:
            raise ValueError("async execution runtime requires at least one managed service")
        self.application = application
        self.tasks = tasks
        self.supervisor = supervisor or AsyncServiceSupervisor()
        self.started = False

    async def start(self) -> None:
        if self.started:
            raise RuntimeError("async execution runtime is already started")
        self.application.start()
        try:
            await self.supervisor.start(self.tasks)
            await asyncio.sleep(0)
            failed = tuple(item for item in self.supervisor.snapshots()
                           if item.criticality is ServiceCriticality.CRITICAL and item.status.value == "failed")
            if failed:
                raise RuntimeError("critical managed service failed during startup: " + ",".join(item.name for item in failed))
            self.application.run()
            self.started = True
        except Exception:
            await self.supervisor.stop()
            self.application.stop()
            raise

    async def wait_for_critical_fault(self) -> ServiceFault:
        if not self.started:
            raise RuntimeError("async execution runtime is not started")
        fault = await self.supervisor.wait_critical_fault()
        if self.application.status in {RuntimeStatus.RUNNING, RuntimeStatus.DEGRADED, RuntimeStatus.REDUCE_ONLY}:
            self.application.degrade(
                f"managed service {fault.task_name} failed: {fault.error_type}: {fault.message}",
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

    def service_snapshots(self) -> tuple[ManagedServiceSnapshot, ...]:
        return self.supervisor.snapshots()

    def task_snapshots(self) -> tuple[ManagedServiceSnapshot, ...]:
        return self.service_snapshots()
