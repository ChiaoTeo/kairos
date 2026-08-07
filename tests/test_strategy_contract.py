from __future__ import annotations

from decimal import Decimal

import pytest

from kairospy.strategy import (
    EventEnvelope,
    StrategyBase,
    StrategyContractError,
    SubscriptionRequest,
    TargetPositionRequest,
    validate_strategy,
)


def test_public_strategy_contract_owns_typed_requests() -> None:
    subscription = SubscriptionRequest("BTCUSDT", selectors=("quote",))
    target = TargetPositionRequest("BTCUSDT", Decimal("1.25"), account_id="main")

    assert subscription.subject == "BTCUSDT"
    assert subscription.selectors == ("quote",)
    assert target.quantity == Decimal("1.25")


def test_strategy_validation_requires_the_complete_lifecycle() -> None:
    class Incomplete:
        strategy_id = "incomplete"

    with pytest.raises(StrategyContractError, match="missing lifecycle callbacks"):
        validate_strategy(Incomplete())


def test_strategy_base_implements_the_public_lifecycle() -> None:
    strategy = StrategyBase()
    validate_strategy(strategy)
    assert strategy.strategy_id == "strategy"


def test_event_envelope_is_the_public_callback_event() -> None:
    event = EventEnvelope("market.events", 1, "data", "quote", payload={})
    assert event.sequence == 1
