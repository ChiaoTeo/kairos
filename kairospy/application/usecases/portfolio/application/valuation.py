"""Cross-account portfolio valuation use case."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from kairospy.application.usecases.portfolio.protocol import PortfolioRateProvider
from kairospy.domain.account import AccountState


class PortfolioAvailability(StrEnum):
    READY = "ready"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class PortfolioCurrencyValue:
    currency: str
    native_amount: Decimal
    valuation_amount: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioExposure:
    instrument_id: str
    currency: str
    native_notional: Decimal
    valuation_notional: Decimal | None


@dataclass(frozen=True, slots=True)
class PortfolioValuationRequest:
    states: tuple[AccountState, ...]
    valuation_currency: str

    def __post_init__(self) -> None:
        if not self.valuation_currency.strip():
            raise ValueError("valuation currency cannot be empty")


@dataclass(frozen=True, slots=True)
class PortfolioValuationResult:
    availability: PortfolioAvailability
    valuation_currency: str
    values: tuple[PortfolioCurrencyValue, ...]
    equity: Decimal | None
    unavailable_currencies: tuple[str, ...] = ()
    exposures: tuple[PortfolioExposure, ...] = ()
    unrealized_pnl: Decimal | None = None
    unavailable_positions: tuple[str, ...] = ()


class PortfolioValuationApplication:
    def __init__(self, rates: PortfolioRateProvider) -> None:
        self._rates = rates

    def value(self, request: PortfolioValuationRequest) -> PortfolioValuationResult:
        native: dict[str, Decimal] = {}
        for state in request.states:
            for balance in state.balances:
                native[balance.currency] = native.get(balance.currency, Decimal("0")) + balance.total
            for liability in state.liabilities:
                native[liability.currency] = native.get(liability.currency, Decimal("0")) - liability.principal - liability.interest

        values: list[PortfolioCurrencyValue] = []
        unavailable: list[str] = []
        exposures: list[PortfolioExposure] = []
        unavailable_positions: list[str] = []
        equity = Decimal("0")
        for currency, amount in sorted(native.items()):
            rate = Decimal("1") if currency == request.valuation_currency else self._rates.rate(currency, request.valuation_currency)
            converted = None if rate is None else amount * rate
            values.append(PortfolioCurrencyValue(currency, amount, converted))
            if converted is None:
                unavailable.append(currency)
            else:
                equity += converted

        unrealized_pnl = Decimal("0")
        pnl_complete = True
        for state in request.states:
            for position in state.positions:
                instrument_id = str(position.instrument_id)
                currency = position.margin_currency
                if currency is None or position.mark_price is None:
                    unavailable_positions.append(instrument_id)
                    continue
                native_notional = position.quantity * position.mark_price
                rate = Decimal("1") if currency == request.valuation_currency else self._rates.rate(currency, request.valuation_currency)
                valuation_notional = None if rate is None else native_notional * rate
                exposures.append(PortfolioExposure(instrument_id, currency, native_notional, valuation_notional))
                if valuation_notional is None:
                    unavailable_positions.append(instrument_id)
                if position.unrealized_pnl is None:
                    pnl_complete = False
                else:
                    pnl_rate = Decimal("1") if currency == request.valuation_currency else self._rates.rate(currency, request.valuation_currency)
                    if pnl_rate is None:
                        pnl_complete = False
                    else:
                        unrealized_pnl += position.unrealized_pnl * pnl_rate

        if unavailable or unavailable_positions:
            availability = PortfolioAvailability.UNAVAILABLE
        else:
            availability = PortfolioAvailability.READY
        if unavailable or unavailable_positions or not pnl_complete:
            resolved_equity = None
        else:
            resolved_equity = equity + unrealized_pnl
        return PortfolioValuationResult(
            availability,
            request.valuation_currency,
            tuple(values),
            resolved_equity,
            tuple(unavailable),
            tuple(exposures),
            unrealized_pnl if pnl_complete else None,
            tuple(unavailable_positions),
        )


__all__ = [
    "PortfolioAvailability",
    "PortfolioCurrencyValue",
    "PortfolioExposure",
    "PortfolioValuationApplication",
    "PortfolioValuationRequest",
    "PortfolioValuationResult",
]
