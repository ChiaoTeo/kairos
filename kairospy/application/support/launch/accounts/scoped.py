from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from kairospy.application.support.runtime.contracts import AccountRuntime
from kairospy.application.support.runtime.contracts import AccountRuntimeEnvelope
from kairospy.core.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountSnapshot, AccountState

if TYPE_CHECKING:
    from kairospy.application.support.launch.accounts import LaunchAccountDirectory


@dataclass(frozen=True, slots=True)
class LaunchScopedAccountRuntime:
    runtime: AccountRuntime
    account_directory: LaunchAccountDirectory

    async def events(self) -> AsyncIterator[AccountRuntimeEnvelope]:
        async for event in self.runtime.events():
            yield event

    def accounts(self) -> tuple[AccountContext, ...]:
        return self.account_directory.contexts()

    def directory(self) -> LaunchAccountDirectory:
        return self.account_directory

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        return self.runtime.snapshot(account)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        return self.runtime.state(account)

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        return tuple(self.runtime.capabilities(account)) if hasattr(self.runtime, "capabilities") else ()

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return tuple(self.runtime.fees(account)) if hasattr(self.runtime, "fees") else ()


__all__ = ["LaunchScopedAccountRuntime"]
