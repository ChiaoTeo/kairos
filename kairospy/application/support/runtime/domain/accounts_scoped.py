from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.domain.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountSnapshot, AccountState

if TYPE_CHECKING:
    from kairospy.application.support.runtime.domain.accounts import RuntimeAccountDirectory


@dataclass(frozen=True, slots=True)
class RuntimeScopedAccountRuntime:
    runtime: object
    account_directory: RuntimeAccountDirectory

    async def events(self) -> AsyncIterator[RuntimeEnvelope[object]]:
        async for event in self.runtime.events():
            yield event

    def accounts(self) -> tuple[AccountContext, ...]:
        return self.account_directory.contexts()

    def directory(self) -> RuntimeAccountDirectory:
        return self.account_directory

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        return self.runtime.snapshot(account)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        return self.runtime.state(account)

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        return tuple(self.runtime.capabilities(account)) if hasattr(self.runtime, "capabilities") else ()

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return tuple(self.runtime.fees(account)) if hasattr(self.runtime, "fees") else ()


__all__ = ["RuntimeScopedAccountRuntime"]
