from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.launch import LaunchAccountDirectory
from kairospy.core.account import AccountBookRef, AccountCapability, AccountContext, AccountFeeSchedule, AccountSnapshot, AccountState


class AccountPort(Protocol):
    def events(self) -> AsyncIterator[RuntimeEnvelope]:
        ...

    def accounts(self) -> tuple[AccountContext, ...]:
        ...

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        ...

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        ...

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        ...

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        ...

    def directory(self) -> LaunchAccountDirectory:
        ...


@dataclass(frozen=True, slots=True)
class LaunchScopedAccountPort:
    port: AccountPort
    account_directory: LaunchAccountDirectory

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        async for event in self.port.events():
            yield event

    def accounts(self) -> tuple[AccountContext, ...]:
        return self.account_directory.contexts()

    def directory(self) -> LaunchAccountDirectory:
        return self.account_directory

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        return self.port.snapshot(account)

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        return self.port.state(account)

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        return tuple(self.port.capabilities(account))

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return tuple(self.port.fees(account))


__all__ = ["AccountPort", "LaunchScopedAccountPort"]
