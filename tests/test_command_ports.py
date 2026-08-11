from __future__ import annotations

from decimal import Decimal

from kairospy.strategy import (
    ArbitrageLegRequest,
    HedgePolicy,
    MakerExecutionPolicy,
    MarketSubscriptionRequest,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    PortfolioRebalanceTarget,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
)
from kairospy.infrastructure.contracts.execution import intent_port, query_port
from kairospy.infrastructure.contracts.market import command_port
from kairospy.infrastructure.transport import (
    ExecutionIntentCommandPort,
    MarketUnixCommandPort,
)


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method, path, body):
        self.calls.append((method, path, body))
        return 202, {"status": "accepted", "command_id": body["command_id"]}


class RecordingQueryClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, method, path, body=None):
        self.calls.append((method, path))
        if path.startswith("/v1/intent?"):
            return 200, {"intent_id": "intent-1", "status": "executing"}
        if path.startswith("/v1/intent-events"):
            return 200, {"events": [{"status": "accepted"}]}
        return 200, {"intents": [{"intent_id": "intent-1"}]}


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
    assert client.calls[0][2]["operation"] == "market.subscribe"
    assert client.calls[0][2]["strategy_id"] == "sma"
    assert client.calls[0][2]["payload"]["selectors"] == ["quote", "bar:1m"]


def test_market_port_preserves_asset_type_route_key() -> None:
    client = RecordingClient()
    port = MarketUnixCommandPort(client)
    port.subscribe(
        MarketSubscriptionRequest(
            "AAPL", exchange="okx", market_type="spot", asset_type="equity"
        ),
        strategy_id="equity",
        instance_id="instance-1",
        request_id="request-equity",
    )
    assert client.calls[0][2]["payload"]["asset_type"] == "equity"


def test_market_port_forwards_chain_subscription_parameters() -> None:
    client = RecordingClient()
    port = MarketUnixCommandPort(client)
    port.subscribe(
        MarketSubscriptionRequest(
            "market.AAPL",
            selectors=("quote",),
            exchange="massive",
            market_type="options",
            asset_type="equity",
            params={"mode": "chain", "underlying": "AAPL"},
        ),
        strategy_id="options",
        instance_id="instance-1",
        request_id="request-options",
    )
    assert client.calls[0][2]["payload"]["params"] == {
        "mode": "chain",
        "underlying": "AAPL",
    }


def test_execution_port_encodes_decimal_intent_without_vendor_payloads() -> None:
    client = RecordingClient()
    port = ExecutionIntentCommandPort(client)

    handle = port.target_position(
        TargetPositionRequest("BTCUSDT", Decimal("1.250"), account_id="main"),
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-2",
    )

    assert handle.status == "accepted"
    method, path, body = client.calls[0]
    assert (method, path) == ("POST", "/v1/intents/submit")
    assert body["operation"] == "execution.submit_intent"
    assert body["payload"]["intent"]["target_quantity_mantissa"] == 125
    assert body["payload"]["intent"]["quantity_scale"] == 2
    assert body["payload"]["intent"]["strategy_id"] == "sma"


def test_execution_port_applies_launch_live_safety_before_owner_command() -> None:
    client = RecordingClient()
    port = ExecutionIntentCommandPort(
        client, allow_trading=False, require_limit_orders=True
    )

    handle = port.target_position(
        TargetPositionRequest("BTCUSDT", Decimal("1"), account_id="main"),
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-3",
    )

    assert handle.status == "rejected"
    assert "disabled" in (handle.error or "")
    assert client.calls == []


def test_execution_port_encodes_pair_arbitrage_as_two_execution_legs() -> None:
    client = RecordingClient()
    port = ExecutionIntentCommandPort(client)
    handle = port.pair_arbitrage(
        PairArbitrageRequest(
            ArbitrageLegRequest("BTCUSDT", "Buy", Decimal("1"), segment_key="spot"),
            ArbitrageLegRequest("BTC-PERP", "Sell", Decimal("1"), segment_key="perp"),
        ),
        strategy_id="arb",
        instance_id="instance-1",
        request_id="request-pair",
    )
    assert handle.status == "accepted"
    intent = client.calls[0][2]["payload"]["intent"]
    assert intent["intent_type"] == "PairArbitrage"
    assert [leg["side"] for leg in intent["legs"]] == ["Buy", "Sell"]


def test_pair_request_exposes_split_maker_and_hedge_controls() -> None:
    client = RecordingClient()
    port = ExecutionIntentCommandPort(client)
    port.pair_arbitrage(
        PairArbitrageRequest(
            ArbitrageLegRequest(
                "USDCUSDT",
                "Buy",
                Decimal("100"),
                split=SplitOrderPolicy(child_count=4, interval_millis=50),
                maker=MakerExecutionPolicy(
                    min_interval_millis=25, max_inventory_abs=Decimal("200")
                ),
            ),
            ArbitrageLegRequest("USDCUSDT-PERP", "Sell", Decimal("100")),
            hedge_policy=HedgePolicy(
                "leg-0", "leg-1", max_unhedged_quantity=Decimal("1")
            ),
        ),
        strategy_id="maker",
        instance_id="instance-1",
        request_id="request-maker",
    )
    intent = client.calls[0][2]["payload"]["intent"]
    assert intent["legs"][0]["options"]["split"]["child_count"] == 4
    assert intent["legs"][0]["options"]["maker"]["max_inventory_abs_mantissa"] == 200
    assert intent["hedge_policy"]["leader_leg_id"] == "leg-0"


def test_quote_provisioning_is_a_two_sided_execution_intent() -> None:
    client = RecordingClient()
    port = ExecutionIntentCommandPort(client)
    handle = port.quote_provisioning(
        QuoteProvisioningRequest(
            "USDCUSDT",
            Decimal("0.9998"),
            Decimal("100"),
            Decimal("1.0002"),
            Decimal("100"),
            maker=MakerExecutionPolicy(min_interval_millis=100),
        ),
        strategy_id="maker",
        instance_id="instance-1",
        request_id="request-quote",
    )
    assert handle.status == "accepted"
    intent = client.calls[0][2]["payload"]["intent"]
    assert intent["intent_type"] == "QuoteProvisioning"
    assert [leg["side"] for leg in intent["legs"]] == ["Buy", "Sell"]


def test_quote_refresh_uses_execution_owner_replace_boundary() -> None:
    client = RecordingClient()
    port = ExecutionIntentCommandPort(client)
    handle = port.refresh_quote(
        QuoteRefreshRequest(
            "intent:quote",
            Decimal("0.9998"),
            Decimal("1.0002"),
            quote_observed_at_unix_nanos=123,
        ),
        strategy_id="maker",
        instance_id="instance-1",
        request_id="request-refresh",
    )
    assert handle.status == "accepted"
    assert client.calls[0][1] == "/v1/intents/refresh-quote"
    assert client.calls[0][2]["payload"]["bid_price_mantissa"] == 9998
    assert client.calls[0][2]["payload"]["ask_price_mantissa"] == 10002


def test_execution_port_encodes_portfolio_targets_as_target_position_legs() -> None:
    client = RecordingClient()
    port = ExecutionIntentCommandPort(client)
    handle = port.portfolio_rebalance(
        PortfolioRebalanceRequest(
            (
                PortfolioRebalanceTarget("BTCUSDT", Decimal("2")),
                PortfolioRebalanceTarget("ETHUSDT", Decimal("5")),
            ),
        ),
        strategy_id="portfolio",
        instance_id="instance-1",
        request_id="request-portfolio",
    )
    assert handle.status == "accepted"
    intent = client.calls[0][2]["payload"]["intent"]
    assert intent["intent_type"] == "PortfolioRebalance"
    assert all(leg["target_position"] for leg in intent["legs"])


def test_contract_facades_construct_the_strategy_command_ports(tmp_path) -> None:
    market = command_port(tmp_path / "market.sock", launch_id="launch-1")
    execution = intent_port(tmp_path / "execution.sock", launch_id="launch-1")

    assert isinstance(market, MarketUnixCommandPort)
    assert isinstance(execution, ExecutionIntentCommandPort)
    assert market.launch_id == "launch-1"
    assert execution.launch_id == "launch-1"


def test_execution_query_port_exposes_intent_state_and_events() -> None:
    client = RecordingQueryClient()
    from kairospy.infrastructure.transport.commands import ExecutionIntentQueryPort

    query = ExecutionIntentQueryPort(client)
    assert query.get_intent("intent-1")["status"] == "executing"
    assert query.list_intents()[0]["intent_id"] == "intent-1"
    assert query.intent_events("intent-1")[0]["status"] == "accepted"
    assert client.calls[0][1] == "/v1/intent?intent_id=intent-1"
