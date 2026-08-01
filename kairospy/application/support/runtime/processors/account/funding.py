from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.services.application import RuntimeAccountService
from kairospy.core.market import MarketEvent, RateObservation
from kairospy.core.reference import MarketId, reference_slug


class FundingProcessor:
    def __init__(
        self,
        *,
        service: RuntimeAccountService,
    ) -> None:
        if not service.settlement_currency.strip():
            raise ValueError("funding processor settlement_currency is required")
        self.service = service
        self.settlement_currency = service.settlement_currency
        self._applied: set[tuple[str, datetime]] = set()

    def on_event(self, event: RuntimeEnvelope) -> None:
        rate = _funding_rate(event.payload)
        if rate is None:
            return
        instrument_id = _instrument_id(rate)
        if instrument_id is None:
            return
        mark_price = rate.mark_price
        if mark_price is None:
            return
        position = self.service.positions().get(instrument_id, Decimal("0"))
        if position == 0 or rate.rate == 0:
            return
        key = (str(rate.market_id or rate.rate_id), rate.time)
        if key in self._applied:
            return
        cash_delta = -(position * mark_price * rate.rate)
        if cash_delta == 0:
            return
        self.service.record_funding(
            occurred_at=rate.time,
            currency=self.settlement_currency,
            cash_delta=cash_delta,
            instrument_id=instrument_id,
            reference_id=f"funding:{rate.market_id or rate.rate_id}:{rate.time.isoformat()}",
        )
        self._applied.add(key)


def _funding_rate(payload: object) -> RateObservation | None:
    value = payload.value if isinstance(payload, MarketEvent) else payload
    if not isinstance(value, RateObservation):
        return None
    basis = value.basis.strip().lower()
    if basis == "funding_rate":
        return value
    if isinstance(payload, MarketEvent) and payload.kind == "funding_rate":
        return value
    return None


def _instrument_id(rate: RateObservation) -> str | None:
    if rate.instrument_id is not None:
        return str(rate.instrument_id)
    if rate.market_id is None:
        return None
    return _instrument_id_from_market_id(rate.market_id)


def _instrument_id_from_market_id(value: MarketId | str) -> str | None:
    parts = str(value).split(":")
    if len(parts) < 4 or parts[0] != "market":
        return None
    market = reference_slug(parts[2])
    symbol = reference_slug(":".join(parts[3:]))
    tokens = symbol.split("_", 1)
    if len(tokens) != 2:
        return None
    return f"instrument:{market}:{tokens[0]}:{tokens[1]}"


__all__ = ["FundingProcessor"]
