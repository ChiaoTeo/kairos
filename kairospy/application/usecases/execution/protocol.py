"""Ports consumed by the execution usecase."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from kairospy.domain.account import AccountContext
from kairospy.domain.execution import ExecutionUpdate


class ExecutionUpdateSource(Protocol):
    def events(
        self,
        account: AccountContext,
        *,
        symbol: str | None = None,
    ) -> AsyncIterator[ExecutionUpdate]: ...


__all__ = [
    "ExecutionUpdateSource",
]
