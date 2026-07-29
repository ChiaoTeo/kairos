from __future__ import annotations

from decimal import Decimal


class AccountPerformanceMixin:
    account_view: object | None

    @property
    def initial_equity(self) -> Decimal:
        value = getattr(self.account_view, "initial_equity", None)
        if value is not None:
            return Decimal(str(value))
        value = getattr(self.account_view, "cash", None)
        return Decimal("0") if value is None else Decimal(str(value))

    @property
    def final_equity(self) -> Decimal:
        value = getattr(self.account_view, "equity", None)
        return Decimal("0") if value is None else Decimal(str(value))

    @property
    def net_profit(self) -> Decimal:
        value = getattr(self.account_view, "net_profit", None)
        return self.final_equity - self.initial_equity if value is None else Decimal(str(value))

    @property
    def total_return(self) -> Decimal:
        value = getattr(self.account_view, "total_return", None)
        if value is not None:
            return Decimal(str(value))
        return Decimal("0") if self.initial_equity == 0 else self.net_profit / self.initial_equity


__all__ = ["AccountPerformanceMixin"]
