from __future__ import annotations

from datetime import timezone
from decimal import Decimal

import pytest

from kairospy.investment.apps.market.application.events import MarketEventRecord
from kairospy.investment.apps.execution.application.mapping import (
    map_execution_fill,
    map_execution_intent,
    map_execution_order,
)
from kairospy.investment.apps.market.application.mapping import map_market_event
from kairospy.strategy import BarEvent, GreeksEvent, IntentStatus, OrderStatus


class _DecimalAccessor:
    def __init__(self, mantissa: int, scale: int) -> None:
        self._mantissa = mantissa
        self._scale = scale

    def Mantissa(self) -> int:
        return self._mantissa

    def Scale(self) -> int:
        return self._scale


class _MarketScopeAccessor:
    def __init__(self, market_id: str) -> None:
        self._market_id = market_id.encode()

    def Kind(self) -> int:
        return 1

    def MarketId(self) -> bytes:
        return self._market_id


class _BarAccessor:
    def __init__(
        self,
        *,
        instrument_id: str = "instrument:test:SPY",
        market_id: str = "market:test:SPY",
        event_time_unix_nanos: int = 1_704_067_200_123_456_789,
        open_value: tuple[int, int] = (123456789, 6),
        high_value: tuple[int, int] = (124000000, 6),
        low_value: tuple[int, int] = (123000000, 6),
        close_value: tuple[int, int] = (123999999, 6),
    ) -> None:
        self._instrument_id = instrument_id.encode()
        self._scope = _MarketScopeAccessor(market_id)
        self._event_time = event_time_unix_nanos
        self._open = _DecimalAccessor(*open_value)
        self._high = _DecimalAccessor(*high_value)
        self._low = _DecimalAccessor(*low_value)
        self._close = _DecimalAccessor(*close_value)

    def InstrumentId(self) -> bytes:
        return self._instrument_id

    def Scope(self) -> _MarketScopeAccessor:
        return self._scope

    def BarSpecId(self) -> bytes:
        return b"1h"

    def Open(self) -> _DecimalAccessor:
        return self._open

    def High(self) -> _DecimalAccessor:
        return self._high

    def Low(self) -> _DecimalAccessor:
        return self._low

    def Close(self) -> _DecimalAccessor:
        return self._close

    def Volume(self) -> None:
        return None

    def SourceObservedAtUnixNanos(self) -> int:
        return self._event_time

    def Provider(self) -> bytes:
        return b"test"


class _GreeksAccessor:
    def __init__(self) -> None:
        self._scope = _MarketScopeAccessor("market:test:SPY-PUT")

    def InstrumentId(self) -> bytes:
        return b"instrument:test:SPY-PUT"

    def Scope(self) -> _MarketScopeAccessor:
        return self._scope

    def ExpiryUnixNanos(self) -> int:
        return 1_710_000_000_000_000_000

    def Strike(self) -> _DecimalAccessor:
        return _DecimalAccessor(45000, 2)

    def Delta(self) -> _DecimalAccessor:
        return _DecimalAccessor(-250000, 6)

    def Gamma(self) -> _DecimalAccessor:
        return _DecimalAccessor(1250, 6)

    def Vega(self) -> _DecimalAccessor:
        return _DecimalAccessor(123456, 6)

    def Theta(self) -> _DecimalAccessor:
        return _DecimalAccessor(-654321, 6)

    def ImpliedVolatility(self) -> _DecimalAccessor:
        return _DecimalAccessor(234567, 6)

    def SourceObservedAtUnixNanos(self) -> int:
        return 1_704_067_200_123_456_789

    def Provider(self) -> bytes:
        return b"test"

    def DerivationId(self) -> bytes:
        return b"provider"


def test_market_mapper_preserves_decimal_precision_and_unix_nanos() -> None:
    raw = MarketEventRecord(
        "market.events",
        7,
        "bar",
        _BarAccessor(),
    )
    event = map_market_event(raw)
    assert isinstance(event, BarEvent)
    assert event.data.open == Decimal("123.456789")
    assert event.data.close == Decimal("123.999999")
    assert event.data.occurred_at.tzinfo is timezone.utc
    assert event.data.occurred_at_unix_nanos == 1_704_067_200_123_456_789
    assert event.metadata.sequence == 7


def test_market_mapper_rejects_discriminator_payload_mismatch() -> None:
    raw = MarketEventRecord(
        "market.events",
        1,
        "quote",
        _BarAccessor(
            event_time_unix_nanos=1,
            open_value=(1, 0),
            high_value=(1, 0),
            low_value=(1, 0),
            close_value=(1, 0),
        ),
    )
    with pytest.raises(ValueError, match="does not match"):
        map_market_event(raw)


def test_market_mapper_exposes_option_greeks_without_losing_precision() -> None:
    raw = MarketEventRecord(
        "market.events",
        8,
        "greeks",
        _GreeksAccessor(),
    )

    event = map_market_event(raw)

    assert isinstance(event, GreeksEvent)
    assert event.data.strike == Decimal("450.00")
    assert event.data.delta == Decimal("-0.250000")
    assert event.data.implied_volatility == Decimal("0.234567")
    assert event.data.occurred_at_unix_nanos == 1_704_067_200_123_456_789


def test_execution_mapper_builds_intent_order_and_fill_models() -> None:
    intent = map_execution_intent(
        {
            "strategy_id": "strategy-a",
            "intent": {
                "intent_id": "intent-1",
                "instrument_id": "instrument:test:SPY",
                "account_ids": ["paper"],
                "target_quantity": "2.5",
                "reason": "test",
            },
            "status": "Executing",
            "order_ids": ["order-1"],
        },
    )
    order = map_execution_order(
        {
            "order_id": "order-1",
            "strategy_id": "strategy-a",
            "intent_id": "intent-1",
            "instrument_id": "instrument:test:SPY",
            "account_id": "paper",
            "side": "Buy",
            "quantity": "2.5",
            "filled_quantity": "1.25",
            "limit_price": "100.01",
            "status": "PartiallyFilled",
            "updated_at_unix_nanos": 1_704_067_200_000_000_000,
        },
    )
    fill = map_execution_fill(
        {
            "fill_id": "fill-1",
            "order_id": "order-1",
            "instrument_id": "instrument:test:SPY",
            "quantity": "1.25",
            "price": "100.01",
            "occurred_at_unix_nanos": 1_704_067_200_000_000_000,
        }
    )
    assert intent.status is IntentStatus.EXECUTING
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("1.25")
    assert fill.price == Decimal("100.01")


def test_execution_mapper_rejects_legacy_decimal_objects() -> None:
    with pytest.raises(ValueError, match="canonical string"):
        map_execution_order(
            {
                "order_id": "order-legacy",
                "strategy_id": "strategy-a",
                "instrument_id": "instrument:test:SPY",
                "account_id": "paper",
                "side": "Buy",
                "quantity": {"mantissa": 42110, "scale": 0},
                "filled_quantity": "0",
                "status": "Accepted",
                "updated_at_unix_nanos": 1,
            },
        )
