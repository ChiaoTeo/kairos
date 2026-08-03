from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from kairospy.application.support.runtime.domain.events import RuntimeEnvelope
from kairospy.application.support.runtime.domain.accounts import RuntimeAccountDirectory
from kairospy.application.usecases.account.services.service import AccountService
from kairospy.application.usecases.account.domain.simulated import SimulatedAccount
from kairospy.domain.account import (
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


class SimulatedAccountService:
    def __init__(
        self,
        account: SimulatedAccount,
        coordinator: object,
        *,
        initialized_at: datetime | None = None,
        directory: RuntimeAccountDirectory | None = None,
        capabilities: tuple[AccountCapability, ...] = (),
        fees: tuple[AccountFeeSchedule, ...] = (),
    ) -> None:
        self.account = account
        self.coordinator = coordinator
        self.initialized_at = initialized_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
        self._directory = directory
        contexts = (account.context,) if directory is None else directory.contexts()
        self.accounts_service = AccountService(
            contexts,
            coordinator=coordinator,
            capabilities=capabilities,
            fees=fees,
            provision_missing_capabilities=False,
        )
        self._deposit_initial_cash()

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def accounts(self) -> tuple[AccountContext, ...]:
        return self.accounts_service.accounts()

    def directory(self) -> RuntimeAccountDirectory:
        return self._directory or RuntimeAccountDirectory.from_contexts((self.account.context,))

    def capabilities(self, account: AccountBookRef | None = None) -> tuple[AccountCapability, ...]:
        return self.accounts_service.capabilities(account)

    def fees(self, account: AccountBookRef | None = None) -> tuple[AccountFeeSchedule, ...]:
        return self.accounts_service.fees(account)

    def snapshot(self, account: AccountBookRef | None = None) -> AccountSnapshot | None:
        context = self._context_for(account)
        if context is None:
            return None
        snapshot = self.accounts_service.snapshot(context.book)
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
        snapshot = self.accounts_service.snapshot(context.book)
        if context.book != self.account.context.book:
            if snapshot is not None:
                return self.coordinator.account_projection(context, venue_snapshot=snapshot)
            return AccountState(context, (), (), (), (), self.initialized_at, AccountSource.SIMULATED)
        return self.coordinator.account_projection(self.account.context, venue_snapshot=snapshot)

    def update_snapshot(self, snapshot: AccountSnapshot) -> None:
        if snapshot.context not in self.accounts():
            raise ValueError("simulated account snapshot context does not match service account directory")
        self.accounts_service.update_snapshot(snapshot)

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
