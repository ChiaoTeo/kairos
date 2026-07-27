from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from kairospy.accounts import AccountBootstrapResult, AccountContext, AccountDifference
from kairospy.execution import ExecutionCoordinator
from kairospy.runtime import AccountCurrentView, AccountRuntimeEvent, StrategyRunResult, SystemRuntimeEvent


@dataclass(frozen=True, slots=True)
class LiveRunResult:
    account: AccountContext
    runtime: StrategyRunResult
    bootstrap: AccountBootstrapResult
    coordinator: ExecutionCoordinator
    account_view: AccountCurrentView
    incidents: tuple[SystemRuntimeEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveReconciliationResult:
    bootstrap: AccountBootstrapResult
    differences: tuple[AccountDifference, ...]
    event: AccountRuntimeEvent


@dataclass(frozen=True, slots=True)
class LiveLoopIteration:
    iteration: int
    started_at: datetime
    finished_at: datetime
    result: LiveRunResult | None = None
    incidents: tuple[SystemRuntimeEvent, ...] = ()
    error: str = ""

    @property
    def succeeded(self) -> bool:
        return self.result is not None and not self.error


@dataclass(frozen=True, slots=True)
class LiveLoopResult:
    iterations: tuple[LiveLoopIteration, ...]

    @property
    def latest(self) -> LiveRunResult | None:
        for iteration in reversed(self.iterations):
            if iteration.result is not None:
                return iteration.result
        return None

    @property
    def incidents(self) -> tuple[SystemRuntimeEvent, ...]:
        return tuple(incident for iteration in self.iterations for incident in iteration.incidents)

    @property
    def succeeded_count(self) -> int:
        return sum(1 for iteration in self.iterations if iteration.succeeded)


class LiveStopToken:
    def __init__(self) -> None:
        self._requested = False
        self._reason = ""

    @property
    def requested(self) -> bool:
        return self._requested

    @property
    def reason(self) -> str:
        return self._reason

    def request_stop(self, reason: str = "") -> None:
        self._requested = True
        self._reason = reason


LiveLoopHeartbeatStatus = Literal["starting", "succeeded", "failed", "stopped"]


@dataclass(frozen=True, slots=True)
class LiveLoopHeartbeat:
    status: LiveLoopHeartbeatStatus
    iteration: int
    occurred_at: datetime
    account: AccountContext
    error: str = ""
    stop_reason: str = ""
    consecutive_failures: int = 0


class LiveLoopMonitor(Protocol):
    def heartbeat(self, event: LiveLoopHeartbeat) -> None:
        ...


__all__ = [
    "LiveLoopIteration",
    "LiveLoopHeartbeat",
    "LiveLoopMonitor",
    "LiveLoopResult",
    "LiveReconciliationResult",
    "LiveRunResult",
    "LiveStopToken",
]
