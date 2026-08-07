from __future__ import annotations

from decimal import Decimal

from kairospy.strategy import MarketSubscriptionRequest, TargetPositionRequest
from kairospy.infrastructure.transport import AccountIntentCommandPort, MarketUnixCommandPort


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method, path, body):
        self.calls.append((method, path, body))
        return 202, {"status": "accepted", "request_id": body["request_id"]}


def test_market_port_adapts_typed_subscription_to_owner_command() -> None:
    client = RecordingClient()
    port = MarketUnixCommandPort(client)

    handle = port.subscribe(
        MarketSubscriptionRequest("BTCUSDT", selectors=("quote", "bar:1m")),
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-1",
    )

    assert handle.status == "accepted"
    assert client.calls[0][1] == "/v1/subscribe"
    assert client.calls[0][2]["strategy_id"] == "sma"
    assert client.calls[0][2]["selectors"] == ["quote", "bar:1m"]


def test_account_port_encodes_decimal_intent_without_vendor_payloads() -> None:
    client = RecordingClient()
    port = AccountIntentCommandPort(client)

    handle = port.target_position(
        TargetPositionRequest("BTCUSDT", Decimal("1.250"), account_id="main"),
        strategy_id="sma",
        request_id="request-2",
    )

    assert handle.status == "accepted"
    method, path, body = client.calls[0]
    assert (method, path) == ("POST", "/v1/intents/submit")
    assert body["intent"]["target_quantity"] == {"mantissa": 125, "scale": 2}
    assert body["intent"]["strategy_id"] == "sma"


def test_account_port_applies_launch_live_safety_before_owner_command() -> None:
    client = RecordingClient()
    port = AccountIntentCommandPort(client, allow_trading=False, require_limit_orders=True)

    handle = port.target_position(
        TargetPositionRequest("BTCUSDT", Decimal("1"), account_id="main"),
        strategy_id="sma",
        request_id="request-3",
    )

    assert handle.status == "rejected"
    assert "disabled" in (handle.error or "")
    assert client.calls == []
