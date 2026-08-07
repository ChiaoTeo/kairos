from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from kairospy.strategy import (
    MarketSubscriptionRequest,
    TargetPositionRequest,
)
from kairospy.application.strategy.services import StrategyContextBus
from kairospy.application.strategy.domain.messages import CommandHandle, ContextRequest, StrategySignal


@dataclass
class MarketPort:
    subscriptions: list[tuple[MarketSubscriptionRequest, str, str, str]]

    def subscribe(self, request, *, strategy_id, instance_id, request_id):
        self.subscriptions.append((request, strategy_id, instance_id, request_id))
        return CommandHandle(request_id, "pending")

    def unsubscribe(self, subscription, *, strategy_id, request_id):
        return CommandHandle(request_id, "accepted")


@dataclass
class IntentPort:
    targets: list[tuple[TargetPositionRequest, str, str]]
    signals: list[StrategySignal]

    def target_position(self, request, *, strategy_id, request_id):
        self.targets.append((request, strategy_id, request_id))
        return CommandHandle(request_id, "accepted")

    def publish(self, signal):
        self.signals.append(signal)
        return CommandHandle(f"{signal.strategy_id}:signal:1", "accepted")


def test_context_bus_routes_typed_requests_with_instance_identity() -> None:
    market = MarketPort([])
    intents = IntentPort([], [])
    bus = StrategyContextBus(market=market, intents=intents)

    subscription = MarketSubscriptionRequest("BTCUSDT", selectors=("quote",))
    subscribe = bus.submit(ContextRequest("market.subscribe", subscription, "sma", "r-1", "i-1"))
    target = TargetPositionRequest("BTCUSDT", Decimal("1.25"), account_id="main")
    intent = bus.submit(ContextRequest("intent.target_position", target, "sma", "r-2", "i-1"))

    assert subscribe.status == "pending"
    assert intent.status == "accepted"
    assert market.subscriptions == [(subscription, "sma", "i-1", "r-1")]
    assert intents.targets == [(target, "sma", "r-2")]
    assert bus.status("r-2") == intent


def test_context_bus_rejects_untyped_or_unknown_operations() -> None:
    bus = StrategyContextBus(market=MarketPort([]), intents=IntentPort([], []))

    wrong_type = bus.submit(ContextRequest("market.subscribe", {"subject": "BTCUSDT"}, "sma", "r-1"))
    unknown = bus.submit(ContextRequest("account.read", object(), "sma", "r-2"))

    assert wrong_type.status == "rejected"
    assert unknown.status == "rejected"
    assert "unsupported" in (unknown.error or "")


def test_context_bus_turns_missing_owner_process_into_a_rejected_handle() -> None:
    class MissingMarket(MarketPort):
        def subscribe(self, request, *, strategy_id, instance_id, request_id):
            raise FileNotFoundError("market socket is absent")

    bus = StrategyContextBus(market=MissingMarket([]), intents=IntentPort([], []))
    handle = bus.submit(ContextRequest(
        "market.subscribe",
        MarketSubscriptionRequest("BTCUSDT"),
        "sma",
        "r-missing",
        "i-1",
    ))

    assert handle.status == "rejected"
    assert "market socket" in (handle.error or "")
