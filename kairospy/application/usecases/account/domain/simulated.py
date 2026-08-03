from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.domain.account import AccountBookKind, AccountBookRef, AccountContext, Environment


@dataclass(frozen=True, slots=True)
class SimulatedAccount:
    account_id: str
    initial_cash: Decimal
    cash_currency: str = "USD"
    broker: str = "simulated"
    environment: Environment = Environment.BACKTEST
    book: AccountBookKind | str = AccountBookKind.DEFAULT
    fee_rate: Decimal = Decimal("0")
    price_field: str = "close"

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ValueError("simulated account_id is required")
        if not self.cash_currency.strip():
            raise ValueError("cash_currency is required")
        if self.initial_cash < 0:
            raise ValueError("initial_cash cannot be negative")
        if self.fee_rate < 0:
            raise ValueError("fee_rate cannot be negative")

    @property
    def context(self) -> AccountContext:
        return AccountContext(AccountBookRef(self.broker, self.account_id, self.book), self.environment)


__all__ = ["SimulatedAccount"]
