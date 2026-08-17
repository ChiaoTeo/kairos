from __future__ import annotations

from datetime import timezone
from decimal import Decimal

import pytest

from kairospy.application.market.events import MarketEventRecord
from kairospy.application.market import ObservationScope
from kairospy.application.execution.mapping import (
    map_execution_fill,
    map_execution_intent,
    map_execution_order,
)
from kairospy.application.market.mapping import map_market_event
from kairospy.infrastructure.transport.market import BarView, DecimalValue, GreeksView
from kairospy.strategy import BarEvent, GreeksEvent, IntentStatus, OrderStatus


def test_market_mapper_preserves_decimal_precision_and_unix_nanos() -> None:
    raw = MarketEventRecord(
        "market.events",
        7,
        "bar",
        BarView(
            "instrument:test:SPY",
            ObservationScope.market("market:test:SPY"),
            "1h",
            DecimalValue(123456789, 6),
            DecimalValue(124000000, 6),
            DecimalValue(123000000, 6),
            DecimalValue(123999999, 6),
            None,
            1_704_067_200_123_456_789,
            "test",
            "provider",
        ),
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
        BarView(
            "instrument:test:SPY",
            ObservationScope.market("market:test:SPY"),
            "1h",
            DecimalValue(1, 0),
            DecimalValue(1, 0),
            DecimalValue(1, 0),
            DecimalValue(1, 0),
            None,
            1,
            None,
            None,
        ),
    )
    with pytest.raises(ValueError, match="does not match"):
        map_market_event(raw)


def test_market_mapper_exposes_option_greeks_without_losing_precision() -> None:
    raw = MarketEventRecord(
        "market.events",
        8,
        "greeks",
        GreeksView(
            "instrument:test:SPY-PUT",
            ObservationScope.market("market:test:SPY-PUT"),
            1_710_000_000_000_000_000,
            DecimalValue(45000, 2),
            DecimalValue(-250000, 6),
            DecimalValue(1250, 6),
            DecimalValue(123456, 6),
            DecimalValue(-654321, 6),
            DecimalValue(234567, 6),
            1_704_067_200_123_456_789,
            "test",
            "provider",
        ),
    )

    event = map_market_event(raw)

    assert isinstance(event, GreeksEvent)
    assert event.data.strike == Decimal("450.00")
    assert event.data.delta == Decimal("-0.250000")
    assert event.data.implied_volatility == Decimal("0.234567")
    assert event.data.occurred_at_unix_nanos == 1_704_067_200_123_456_789


def test_execution_mapper_builds_intent_order_and_fill_projections() -> None:
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
