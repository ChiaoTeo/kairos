from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from kairospy.application.protocol import RuntimeEnvelope
from kairospy.application.runtime.services import RuntimeAccountService
from kairospy.application.domain.execution import SimulatedEquityPoint
from kairospy.application.views import ACCOUNT_EQUITY_CURVE_SCHEMA, AccountRuntimeViewKeys, EquityCurveView
from kairospy.core.market import Bar, MarketEvent, Quote, RateObservation, TradePrint
from kairospy.core.views import ViewStore


class EquityCurveProcessor:
    key = AccountRuntimeViewKeys.equity_curve

    def __init__(
        self,
        *,
        service: RuntimeAccountService,
    ) -> None:
        self.service = service
        self.account = service.account
        self.cash_currency = service.cash_currency
        self.schema = ACCOUNT_EQUITY_CURVE_SCHEMA
        self._marks: dict[str, Decimal] = {}
        self._points: list[SimulatedEquityPoint] = []
        self._last_marker: object | None = None

    def on_event(self, event: RuntimeEnvelope) -> None:
        self._update_mark(event.payload)
        self.record(event.time)

    def on_intents(self, context: object) -> None:
        now = getattr(context, "now", None)
        if now is not None:
            self.record(now)

    def register_views(self, views: ViewStore) -> None:
        if views.registry.get(self.schema.key) is None:
            views.register(self.schema)

    def publish_views(self, views: ViewStore, *, as_of: datetime | None = None) -> None:
        views.put_runtime(self.key, self.view(), as_of=as_of, available_time=as_of)

    def view(self) -> EquityCurveView:
        return EquityCurveView(self.account, tuple(self._points))

    def record(self, at: datetime) -> None:
        cash = self.service.cash(self.cash_currency)
        raw_positions = self.service.positions()
        positions = tuple(sorted((instrument, quantity) for instrument, quantity in raw_positions.items()))
        equity = cash + sum(quantity * self._marks[instrument] for instrument, quantity in positions if instrument in self._marks)
        marker = (at, equity, cash, positions)
        if marker == self._last_marker:
            return
        self._points.append(SimulatedEquityPoint(at, equity, cash, positions))
        self._last_marker = marker

    def _update_mark(self, payload: object) -> None:
        value = payload.value if isinstance(payload, MarketEvent) else payload
        instrument_id = getattr(value, "instrument_id", None)
        if instrument_id is None:
            return
        price = _mark_price(value)
        if price is None or price <= 0:
            return
        self._marks[str(instrument_id)] = price


def _mark_price(value: object) -> Decimal | None:
    if isinstance(value, Bar):
        return value.close
    if isinstance(value, TradePrint):
        return value.price
    if isinstance(value, RateObservation):
        return value.mark_price
    if isinstance(value, Quote):
        return value.midpoint or value.bid or value.ask
    return None


__all__ = ["EquityCurveProcessor"]
