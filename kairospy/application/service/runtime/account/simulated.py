from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.launch import LaunchAccountDirectory
from kairospy.application.ports import AccountPort
from kairospy.application.service.domain.account.simulated import SimulatedAccount
from kairospy.core.account import (
    AccountContext,
    AccountBookRef,
    AccountCapability,
    AccountEvent,
    AccountEventKind,
    AccountFeeSchedule,
    AccountSnapshot,
    AccountSource,
    AccountState,
)
from kairospy.core.execution import ExecutionCoordinator


class SimulatedAccountService(AccountPort):
    def __init__(
        self,
        account: SimulatedAccount,
        coordinator: ExecutionCoordinator,
        *,
        initialized_at: datetime | None = None,
        directory: LaunchAccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
    ) -> None:
        self.account = account
        self.coordinator = coordinator
        self.initialized_at = initialized_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
        self._directory = directory
        self._capabilities = capabilities
        self._fees = fees
        self._snapshots: dict[AccountBookRef, AccountSnapshot] = {}
        self._deposit_initial_cash()

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def accounts(self) -> tuple[AccountContext, ...]:
        return (self.account.context,) if self._directory is None else self._directory.contexts()

    def directory(self) -> LaunchAccountDirectory:
        return self._directory or LaunchAccountDirectory.from_contexts((self.account.context,))

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        if account is None:
            return self._capabilities
        return tuple(item for item in self._capabilities if item.book == account)

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        if account is None:
            return self._fees
        return tuple(item for item in self._fees if item.book == account)

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        context = self._context_for(account)
        if context is None:
            return None
        snapshot = self._snapshots.get(context.book)
        if snapshot is not None:
            return snapshot
        state = self.state(account)
        if state is None:
            return None
        return AccountSnapshot(
            context,
            balances=state.balances,
            margins=state.margins,
            positions=state.positions,
            open_orders=state.open_orders,
            observed_at=state.observed_at,
            source=AccountSource.SIMULATED,
        )

    def state(self, account: AccountBookRef | None = None) -> AccountState | None:
        context = self._context_for(account)
        if context is None:
            return None
        snapshot = self._snapshots.get(context.book)
        if context.book != self.account.context.book:
            if snapshot is not None:
                return self.coordinator.account_projection(context, venue_snapshot=snapshot)
            return AccountState(context, (), (), (), (), self.initialized_at, AccountSource.SIMULATED)
        return self.coordinator.account_projection(self.account.context, venue_snapshot=snapshot)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context not in self.accounts():
            raise ValueError("simulated account snapshot context does not match service account directory")
        self._snapshots[snapshot.context.book] = snapshot

    def _context_for(self, account: AccountBookRef | None) -> AccountContext | None:
        if account is None:
            return self.account.context
        if account == self.account.context.book:
            return self.account.context
        for context in self.accounts():
            if context.book == account:
                return context
        return None

    def _deposit_initial_cash(self) -> None:
        if self.account.initial_cash == 0:
            return
        if self.coordinator.ledger.cash(self.account.context.book).get(self.account.cash_currency, Decimal("0")):
            return
        self.coordinator.ledger.record(
            AccountEvent(
                uuid4(),
                self.account.context.book,
                AccountEventKind.DEPOSIT,
                self.initialized_at,
                self.account.cash_currency,
                cash_delta=self.account.initial_cash,
                reference_id="initial_cash",
            )
        )


__all__ = ["SimulatedAccountService"]
