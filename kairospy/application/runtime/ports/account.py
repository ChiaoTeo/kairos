from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.core.account import AccountContext, AccountRef, AccountSnapshot, AccountState


class AccountPort(Protocol):
    def events(self) -> AsyncIterator[RuntimeEnvelope]:
        ...

    def accounts(self) -> tuple[AccountContext, ...]:
        ...

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        ...

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        ...


__all__ = ["AccountPort"]
