from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4


class RuntimeCommandStatus(StrEnum):
    """Lifecycle of a command submitted to the system."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DEFERRED = "deferred"
    IGNORED = "ignored"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class RuntimeCommand:
    """A generic request crossing the runtime-to-system boundary.

    ``payload`` is deliberately opaque to runtime support.  A system service
    interprets it as one of its own application request types; the runtime
    layer must not know about market, account, or integration objects.
    """

    kind: str
    payload: object | None = None
    command_id: str = field(default_factory=lambda: str(uuid4()))
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actor: str = "strategy"
    correlation_id: str | None = None
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        kind = self.kind.strip().lower()
        if not kind:
            raise ValueError("runtime command kind is required")
        if not self.command_id.strip():
            raise ValueError("runtime command command_id is required")
        if self.requested_at.tzinfo is None:
            raise ValueError("runtime command requested_at must be timezone-aware")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "actor", self.actor.strip() or "strategy")


@dataclass(slots=True)
class CommandHandle:
    """A mutable lifecycle view owned and advanced by ``System``.

    The handle is not the owner of business state.  Its fields only describe
    the command lifecycle and the latest result published by the system.  The
    transition methods are intentionally private-by-convention: callers get a
    handle, while only the system-side registry should advance it.
    """

    command_id: str
    kind: str
    status: RuntimeCommandStatus = RuntimeCommandStatus.PENDING
    accepted_at: datetime | None = None
    completed_at: datetime | None = None
    result: Mapping[str, object] = field(default_factory=dict)
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.command_id.strip():
            raise ValueError("command handle command_id is required")
        if not self.kind.strip():
            raise ValueError("command handle kind is required")
        self.kind = self.kind.strip().lower()
        self.result = MappingProxyType(dict(self.result))

    @property
    def done(self) -> bool:
        return self.status in {
            RuntimeCommandStatus.COMPLETED,
            RuntimeCommandStatus.IGNORED,
            RuntimeCommandStatus.REJECTED,
            RuntimeCommandStatus.CANCELLED,
        }

    @property
    def accepted(self) -> bool:
        return self.status in {
            RuntimeCommandStatus.ACCEPTED,
            RuntimeCommandStatus.DEFERRED,
            RuntimeCommandStatus.COMPLETED,
        }

    def _accept(
        self,
        result: Mapping[str, object] | None = None,
        *,
        at: datetime | None = None,
    ) -> None:
        self._transition(RuntimeCommandStatus.ACCEPTED, result=result, at=at)

    def _complete(self, result: Mapping[str, object] | None = None, *, at: datetime | None = None) -> None:
        self._transition(RuntimeCommandStatus.COMPLETED, result=result, at=at)

    def _defer(self, result: Mapping[str, object] | None = None, *, at: datetime | None = None) -> None:
        self._transition(RuntimeCommandStatus.DEFERRED, result=result, at=at)

    def _ignore(self, result: Mapping[str, object] | None = None, *, at: datetime | None = None) -> None:
        self._transition(RuntimeCommandStatus.IGNORED, result=result, at=at)

    def _reject(self, error: str, *, at: datetime | None = None) -> None:
        self._transition(RuntimeCommandStatus.REJECTED, error=error, at=at)

    def _cancel(self, *, at: datetime | None = None) -> None:
        self._transition(RuntimeCommandStatus.CANCELLED, at=at)

    def _transition(
        self,
        status: RuntimeCommandStatus,
        *,
        result: Mapping[str, object] | None = None,
        error: str | None = None,
        at: datetime | None = None,
    ) -> None:
        if self.done:
            raise RuntimeError(f"command handle {self.command_id} is already {self.status}")
        if status in {RuntimeCommandStatus.ACCEPTED, RuntimeCommandStatus.DEFERRED} and self.status not in {
            RuntimeCommandStatus.PENDING,
            RuntimeCommandStatus.DEFERRED,
        }:
            raise RuntimeError(f"command handle {self.command_id} cannot be accepted from {self.status}")
        if status is not RuntimeCommandStatus.ACCEPTED and self.status not in {
            RuntimeCommandStatus.PENDING,
            RuntimeCommandStatus.DEFERRED,
            RuntimeCommandStatus.ACCEPTED,
        }:
            raise RuntimeError(f"command handle {self.command_id} cannot transition from {self.status}")
        timestamp = at or datetime.now(timezone.utc)
        if timestamp.tzinfo is None:
            raise ValueError("command handle transition time must be timezone-aware")
        self.status = status
        if status in {RuntimeCommandStatus.ACCEPTED, RuntimeCommandStatus.DEFERRED}:
            self.accepted_at = timestamp
        if status in {
            RuntimeCommandStatus.COMPLETED,
            RuntimeCommandStatus.IGNORED,
            RuntimeCommandStatus.REJECTED,
            RuntimeCommandStatus.CANCELLED,
        }:
            self.completed_at = timestamp
        if result is not None:
            self.result = MappingProxyType(dict(result))
        if error is not None:
            self.error = str(error)


__all__ = ["CommandHandle", "RuntimeCommand", "RuntimeCommandStatus"]
