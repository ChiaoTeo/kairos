from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from kairospy.investment.apps.execution.application.mapping import (
    map_execution_fill,
    map_execution_intent,
    map_execution_order,
)
from kairospy.strategy import IntentStatus, OrderStatus


def test_execution_mapper_builds_intent_order_and_fill_models() -> None:
    intent = map_execution_intent(
        SimpleNamespace(
            strategy_id="strategy-a",
            intent=SimpleNamespace(
                intent_id="intent-1",
                instrument_id="instrument:test:SPY",
                account_ids=["paper"],
                target_quantity="2.5",
                reason="test",
            ),
            status="Executing",
            order_ids=["order-1"],
        )
    )
    order = map_execution_order(
        SimpleNamespace(
            order_id="order-1",
            strategy_id="strategy-a",
            intent_id="intent-1",
            instrument_id="instrument:test:SPY",
            account_id="paper",
            side="Buy",
            quantity="2.5",
            filled_quantity="1.25",
            limit_price="100.01",
            status="PartiallyFilled",
            updated_at_unix_nanos=1_704_067_200_000_000_000,
        )
    )
    fill = map_execution_fill(
        SimpleNamespace(
            fill_id="fill-1",
            order_id="order-1",
            instrument_id="instrument:test:SPY",
            quantity="1.25",
            price="100.01",
            occurred_at_unix_nanos=1_704_067_200_000_000_000,
        )
    )
    assert intent.status is IntentStatus.EXECUTING
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity.value == Decimal("1.25")
    assert fill.price.value == Decimal("100.01")


def test_execution_mapper_rejects_legacy_decimal_objects() -> None:
    with pytest.raises(ValueError, match="Execution contract decimal"):
        map_execution_order(
            SimpleNamespace(
                order_id="order-legacy",
                strategy_id="strategy-a",
                instrument_id="instrument:test:SPY",
                account_id="paper",
                side="Buy",
                quantity=SimpleNamespace(mantissa=42110, scale=0),
                filled_quantity="0",
                status="Accepted",
                updated_at_unix_nanos=1,
            )
        )
