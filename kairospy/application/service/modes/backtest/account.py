from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from kairospy.application.runtime.protocol import RuntimeEnvelope
from kairospy.application.runtime.services import AccountService
from kairospy.application.service.domain.account import SimulatedAccount
from kairospy.core.account import (
    AccountContext,
    AccountEvent,
    AccountEventKind,
    AccountRef,
    AccountSnapshot,
    AccountSource,
    AccountState,
)
from kairospy.core.execution import ExecutionCoordinator
from kairospy.core.views import ViewFieldSchema, ViewSchema


@dataclass(frozen=True, slots=True)
class BacktestAccountServiceView:
    account: AccountContext
    cash_currency: str
    initial_cash: Decimal


class BacktestAccountService(AccountService):
    key = "account.service"
    schema = ViewSchema(
        key,
        "system",
        fields=(
            ViewFieldSchema("account", "backtest account context", "runtime state", "backtest account service"),
            ViewFieldSchema("cash_currency", "account cash currency", "runtime state", "backtest account config"),
            ViewFieldSchema("initial_cash", "initial account cash", "run baseline", "backtest account config"),
        ),
        mutability="runtime_writable",
        evidence="runtime backtest account service",
    )

    def __init__(
        self,
        account: SimulatedAccount,
        coordinator: ExecutionCoordinator,
        *,
        initialized_at: datetime | None = None,
    ) -> None:
        self.account = account
        self.coordinator = coordinator
        self.initialized_at = initialized_at or datetime(1970, 1, 1, tzinfo=timezone.utc)
        self._deposit_initial_cash()

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        if False:
            yield

    def accounts(self) -> tuple[AccountContext, ...]:
        return (self.account.context,)

    def snapshot(self, account: AccountRef | None = None) -> AccountSnapshot | None:
        if account is not None and account != self.account.context.account:
            return None
        state = self.state(account)
        if state is None:
            return None
        return AccountSnapshot(
            self.account.context,
            balances=state.balances,
            margins=state.margins,
            positions=state.positions,
            open_orders=state.open_orders,
            observed_at=state.observed_at,
            source=AccountSource.SIMULATED,
        )

    def state(self, account: AccountRef | None = None) -> AccountState | None:
        if account is not None and account != self.account.context.account:
            return None
        return self.coordinator.account_projection(self.account.context)

    def on_event(self, event: RuntimeEnvelope) -> None:
        return None

    def view(self) -> BacktestAccountServiceView:
        return BacktestAccountServiceView(
            self.account.context,
            self.account.cash_currency,
            self.account.initial_cash,
        )

    def _deposit_initial_cash(self) -> None:
        if self.account.initial_cash == 0:
            return
        if self.coordinator.ledger.cash(self.account.context.account).get(self.account.cash_currency, Decimal("0")):
            return
        self.coordinator.ledger.record(
            AccountEvent(
                uuid4(),
                self.account.context.account,
                AccountEventKind.DEPOSIT,
                self.initialized_at,
                self.account.cash_currency,
                cash_delta=self.account.initial_cash,
                reference_id="initial_cash",
            )
        )


__all__ = ["BacktestAccountService", "BacktestAccountServiceView"]
