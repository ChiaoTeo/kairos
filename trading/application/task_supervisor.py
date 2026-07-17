from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Awaitable, Callable


class TaskCriticality(StrEnum):
    OPTIONAL = "optional"
    IMPORTANT = "important"
    CRITICAL = "critical"


class ManagedTaskStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    RESTARTING = "restarting"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPING = "stopping"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class ManagedTaskSpec:
    name: str
    run: Callable[[], Awaitable[None]]
    criticality: TaskCriticality = TaskCriticality.CRITICAL
    restart_limit: int = 0
    allow_completion: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("managed task name cannot be empty")
        if self.restart_limit < 0:
            raise ValueError("managed task restart limit cannot be negative")


@dataclass(frozen=True, slots=True)
class TaskFault:
    task_name: str
    criticality: TaskCriticality
    error_type: str
    message: str
    attempt: int
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ManagedTaskSnapshot:
    name: str
    criticality: TaskCriticality
    status: ManagedTaskStatus
    attempts: int
    restart_count: int
    last_fault: TaskFault | None


@dataclass(slots=True)
class _ManagedTaskState:
    spec: ManagedTaskSpec
    status: ManagedTaskStatus = ManagedTaskStatus.CREATED
    attempts: int = 0
    restart_count: int = 0
    last_fault: TaskFault | None = None
    task: asyncio.Task[None] | None = None


class AsyncTaskSupervisor:
    """Own all long-running tasks and make failures observable to the runtime."""

    def __init__(self) -> None:
        self._states: dict[str, _ManagedTaskState] = {}
        self._faults: asyncio.Queue[TaskFault] = asyncio.Queue()
        self._critical_fault = asyncio.Event()
        self._stopping = False

    async def start(self, specs: tuple[ManagedTaskSpec, ...]) -> None:
        if self._states:
            raise RuntimeError("task supervisor is already started")
        names = [item.name for item in specs]
        if len(names) != len(set(names)):
            raise ValueError("managed task names must be unique")
        self._stopping = False
        for spec in specs:
            state = _ManagedTaskState(spec)
            self._states[spec.name] = state
            state.task = asyncio.create_task(self._run(state), name=f"trader:{spec.name}")
        await asyncio.sleep(0)

    async def _run(self, state: _ManagedTaskState) -> None:
        while not self._stopping:
            state.attempts += 1
            state.status = ManagedTaskStatus.RUNNING
            try:
                await state.spec.run()
                if self._stopping:
                    state.status = ManagedTaskStatus.STOPPED
                    return
                if state.spec.allow_completion:
                    state.status = ManagedTaskStatus.COMPLETED
                    return
                raise RuntimeError("long-running task exited unexpectedly")
            except asyncio.CancelledError:
                state.status = ManagedTaskStatus.STOPPED
                raise
            except Exception as error:
                fault = TaskFault(
                    state.spec.name, state.spec.criticality, type(error).__name__, str(error),
                    state.attempts, datetime.now(timezone.utc),
                )
                state.last_fault = fault
                await self._faults.put(fault)
                if state.restart_count < state.spec.restart_limit and not self._stopping:
                    state.restart_count += 1
                    state.status = ManagedTaskStatus.RESTARTING
                    await asyncio.sleep(0)
                    continue
                state.status = ManagedTaskStatus.FAILED
                if state.spec.criticality is TaskCriticality.CRITICAL:
                    self._critical_fault.set()
                return

    async def stop(self, *, timeout_seconds: float = 5.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("task supervisor stop timeout must be positive")
        self._stopping = True
        tasks = []
        for state in self._states.values():
            if state.task is not None and not state.task.done():
                state.status = ManagedTaskStatus.STOPPING
                state.task.cancel()
                tasks.append(state.task)
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout_seconds)
            except TimeoutError as error:
                raise RuntimeError("managed tasks did not stop before timeout") from error
        for state in self._states.values():
            if state.status is ManagedTaskStatus.STOPPING:
                state.status = ManagedTaskStatus.STOPPED

    async def next_fault(self) -> TaskFault:
        return await self._faults.get()

    async def wait_critical_fault(self) -> TaskFault:
        while True:
            await self._critical_fault.wait()
            faults = tuple(state.last_fault for state in self._states.values() if state.last_fault is not None)
            critical = tuple(item for item in faults if item.criticality is TaskCriticality.CRITICAL)
            if critical:
                return max(critical, key=lambda item: item.occurred_at)
            self._critical_fault.clear()

    def snapshots(self) -> tuple[ManagedTaskSnapshot, ...]:
        return tuple(ManagedTaskSnapshot(
            state.spec.name, state.spec.criticality, state.status, state.attempts,
            state.restart_count, state.last_fault,
        ) for state in sorted(self._states.values(), key=lambda item: item.spec.name))

    @property
    def healthy(self) -> bool:
        return bool(self._states) and all(
            state.status in {ManagedTaskStatus.RUNNING, ManagedTaskStatus.COMPLETED}
            or state.spec.criticality is TaskCriticality.OPTIONAL
            for state in self._states.values()
        )
