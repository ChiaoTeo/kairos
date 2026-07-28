from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.core.account import AccountState, MarginScope

from .reservation import Reservation, ReservationBook


@dataclass(frozen=True, slots=True)
class BuyingPowerCheck:
    accepted: bool
    requested: Decimal
    available: Decimal
    reason: str


class CashBuyingPowerModel:
    def check(
        self,
        account_state: AccountState,
        *,
        currency: str,
        notional: Decimal,
    ) -> BuyingPowerCheck:
        if notional <= 0:
            raise ValueError("order notional must be positive")
        balance = account_state.balance(currency)
        available = balance.free if balance is not None else Decimal("0")
        if available < notional:
            return BuyingPowerCheck(False, notional, available, "insufficient free balance")
        return BuyingPowerCheck(True, notional, available, "accepted by local cash model")


class MarginBuyingPowerModel:
    def check(
        self,
        account_state: AccountState,
        *,
        currency: str,
        instrument_id: str,
        notional: Decimal,
        leverage: Decimal = Decimal("1"),
    ) -> BuyingPowerCheck:
        if notional <= 0:
            raise ValueError("order notional must be positive")
        if leverage <= 0:
            raise ValueError("leverage must be positive")
        required = notional / leverage
        margin = self._margin(account_state, currency=currency, instrument_id=instrument_id)
        available = Decimal("0") if margin is None or margin.available is None else margin.available
        if available < required:
            return BuyingPowerCheck(False, required, available, "insufficient available margin")
        return BuyingPowerCheck(True, required, available, "accepted by local margin model")

    def _margin(self, account_state: AccountState, *, currency: str, instrument_id: str):
        instrument_margin = next(
            (
                margin
                for margin in account_state.margins
                if margin.currency == currency
                and margin.scope in {MarginScope.INSTRUMENT, MarginScope.POSITION}
                and margin.instrument_id == instrument_id
            ),
            None,
        )
        if instrument_margin is not None:
            return instrument_margin
        return next(
            (
                margin
                for margin in account_state.margins
                if margin.currency == currency and margin.scope is MarginScope.ACCOUNT
            ),
            None,
        )


def reserve_cash_order(
    reservations: ReservationBook,
    reservation: Reservation,
    account_state: AccountState,
) -> BuyingPowerCheck:
    check = CashBuyingPowerModel().check(
        account_state,
        currency=reservation.currency,
        notional=reservation.amount,
    )
    if check.accepted:
        reservations.hold(reservation)
    return check


def reserve_margin_order(
    reservations: ReservationBook,
    reservation: Reservation,
    account_state: AccountState,
    *,
    instrument_id: str,
    notional: Decimal,
    leverage: Decimal = Decimal("1"),
) -> BuyingPowerCheck:
    check = MarginBuyingPowerModel().check(
        account_state,
        currency=reservation.currency,
        instrument_id=instrument_id,
        notional=notional,
        leverage=leverage,
    )
    if check.accepted:
        reservations.hold(reservation)
    return check


__all__ = [
    "BuyingPowerCheck",
    "CashBuyingPowerModel",
    "MarginBuyingPowerModel",
    "reserve_cash_order",
    "reserve_margin_order",
]
