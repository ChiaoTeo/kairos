from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Mapping, Protocol

from kairospy.application.support.runtime.domain.commands import CommandHandle, RuntimeCommand


class SystemCallDecision(StrEnum):
    """Admission decision made by System for one call."""

    ACCEPTED = "accepted"
    DEFERRED = "deferred"
    IGNORED = "ignored"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class SystemCallResult:
    """Immutable result of submitting a request to System.

    ``decision`` describes only the admission decision for this call. The
    optional handle owns the request's later lifecycle and may advance across
    multiple System cycles.
    """

    request_id: str
    decision: SystemCallDecision
    handle: CommandHandle | None = None
    result: Mapping[str, object] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("system call request_id is required")
        object.__setattr__(self, "result", MappingProxyType(dict(self.result)))

    @property
    def status(self):
        """Compatibility view of the current handle lifecycle status."""
        return None if self.handle is None else self.handle.status

    @property
    def accepted(self) -> bool:
        return self.decision in {
            SystemCallDecision.ACCEPTED,
            SystemCallDecision.DEFERRED,
        }

    @property
    def done(self) -> bool:
        return self.handle is not None and self.handle.done


class SystemCall(Protocol):
    """Minimal system-call surface carried by a runtime strategy context.

    Runtime owns this protocol because Runtime consumes it.  The concrete
    implementation is supplied by System.  Runtime and a business context do
    not interpret business command kinds.
    """

    def call(self, command: RuntimeCommand) -> SystemCallResult:
        ...

@dataclass(frozen=True, slots=True)
class RuntimeInstruction:
    """Generic instruction returned by the System before a strategy step."""

    action: Literal["dispatch", "hold", "stop"] = "dispatch"
    reason: str | None = None
    strategy_hook: str | None = None

    @property
    def dispatch_strategy(self) -> bool:
        return self.action == "dispatch"


__all__ = [
    "RuntimeInstruction",
    "SystemCall",
    "SystemCallDecision",
    "SystemCallResult",
]
