"""Ports consumed by the execution usecase."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from kairospy.domain.account import AccountRuntimeContext
from kairospy.domain.execution import ExecutionUpdate


class ExecutionUpdateSource(Protocol):
    def events(
        self,
        account: AccountRuntimeContext,
        *,
        symbol: str | None = None,
    ) -> AsyncIterator[ExecutionUpdate]: ...


__all__ = [
    "ExecutionUpdateSource",
]
