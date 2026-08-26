from __future__ import annotations

from decimal import Decimal
import hashlib
import json
import pytest

from kairospy.investment.apps.execution.application import IntentAdmissionEvidence

from kairospy.strategy import (
    ArbitrageLegRequest,
    ExecutionBenchmark,
    HedgePolicy,
    ImmediateAlgorithm,
    MakerTakerHedgeAlgorithm,
    MakerExecutionPolicy,
    PassiveLimitAlgorithm,
    PairArbitrageRequest,
    PortfolioRebalanceRequest,
    PortfolioRebalanceTarget,
    QuoteProvisioningRequest,
    QuoteRefreshRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
    TwapAlgorithm,
    InstrumentId,
    LimitOrderRequest,
    MarketOrderRequest,
    OrderSide,
    ReplaceOrderRequest,
    TimeInForce,
)
from kairospy.investment.apps.market.application import (
    CanonicalMarketTarget,
    ObservationRequirement,
    OptionsTarget,
    Provider,
    ProviderPreference,
    SubscriptionRequest as MarketSubscriptionRequest,
)
from kairospy.investment.apps.execution.application.commands import (
    ExecutionCommandClient,
)
from kairospy.investment.apps.market.application.commands import MarketCommandClient
from kairospy.infrastructure.contracts.execution import ExecutionControlClient
from kairospy.infrastructure.contracts.market import MarketControlClient


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[object] | None]] = []

    def call(self, method, params=None):
        self.calls.append((method, params))
        body = params[0] if params else {}
        if "command_id" not in body:
            return {"sources": []}
        return {"status": "accepted", "command_id": body["command_id"]}


class DirectOrderClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[object] | None]] = []

    def call(self, method, params=None):
        self.calls.append((method, params))
        order_id = params[0] if params else None
        return {"order_id": order_id or "order-1", "status": "accepted"}


def test_execution_control_order_audit_uses_the_durable_query_method() -> None:
    transport = RecordingClient()
    client = ExecutionControlClient.__new__(ExecutionControlClient)
    client._client = transport

    client.order_audit({"order_id": "order-1", "limit": 100})

    assert transport.calls == [
        ("execution_order_audit", [{"order_id": "order-1", "limit": 100}])
    ]


def test_market_port_adapts_typed_subscription_to_owner_command() -> None:
    client = RecordingClient()
    port = MarketCommandClient(client)

    handle = port.subscribe(
        MarketSubscriptionRequest(
            CanonicalMarketTarget("market:binance:spot:BTCUSDT"),
            (ObservationRequirement("quote"), ObservationRequirement("bar", "1m")),
        ),
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-1",
    )

    assert handle.status == "accepted"
    assert client.calls[0][0] == "market_subscribe"
    body = client.calls[0][1][0]
    assert body["schema_version"] == 2
    assert body["operation"] == "subscribe"
    assert body["strategy_id"] == "sma"
    assert body["instance_id"] == "instance-1"
    assert body["payload"]["target"] == {
        "type": "market",
        "market_id": "market:binance:spot:BTCUSDT",
    }
    assert body["payload"]["observations"] == [
        {"kind": "quote", "qualifier": None},
        {"kind": "bar", "qualifier": "1m"},
    ]
    assert body["payload"]["provider_preference"] == {"mode": "automatic"}


def test_market_port_releases_every_subscription_for_strategy_instance() -> None:
    client = RecordingClient()
    port = MarketCommandClient(client, launch_id="launch-1")

    handle = port.release_owner(
        strategy_id="sma",
        instance_id="instance-1",
        request_id="release-1",
    )

    assert handle.status == "accepted"
    method, params = client.calls[0]
    body = params[0]
    assert method == "market_release_owner"
    assert body["schema_version"] == 2
    assert body["operation"] == "release_owner"
    assert body["strategy_id"] == "sma"
    assert body["launch_id"] == "launch-1"
    assert body["payload"] == {}


def test_market_port_preserves_explicit_provider_preference() -> None:
    client = RecordingClient()
    port = MarketCommandClient(client)
    port.subscribe(
        MarketSubscriptionRequest(
            CanonicalMarketTarget("market:sip:equity:US:AAPL"),
            (ObservationRequirement("quote"),),
            ProviderPreference.require(Provider.MASSIVE),
        ),
        strategy_id="equity",
        instance_id="instance-1",
        request_id="request-equity",
    )
    assert client.calls[0][1][0]["payload"]["provider_preference"] == {
        "mode": "require",
        "providers": ["massive"],
    }


def test_market_port_queries_available_data_routes() -> None:
    client = RecordingClient()
    port = MarketCommandClient(client)

    port.data_routes({"market_id": "market:sip:equity:US:AAPL"})

    assert client.calls[0] == (
        "market_data_routes",
        [{"market_id": "market:sip:equity:US:AAPL"}],
    )


def test_market_port_forwards_chain_subscription_parameters() -> None:
    client = RecordingClient()
    port = MarketCommandClient(client)
    port.subscribe(
        MarketSubscriptionRequest(
            OptionsTarget(
                underlying_market_id="market:sip:equity:US:AAPL",
                option_right="both",
                limit=40,
            ),
            (ObservationRequirement("quote"),),
        ),
        strategy_id="options",
        instance_id="instance-1",
        request_id="request-options",
    )
    assert client.calls[0][1][0]["payload"]["target"] == {
        "type": "options",
        "underlying_market_id": "market:sip:equity:US:AAPL",
        "underlying_instrument_id": None,
        "expiry_from_unix_nanos": None,
        "expiry_to_unix_nanos": None,
        "strike_lower": None,
        "strike_upper": None,
        "option_right": "both",
        "limit": 40,
        "progressive": False,
    }


def test_execution_client_encodes_decimal_intent_without_vendor_payloads() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)

    handle = port.target_position(
        TargetPositionRequest(
            "BTCUSDT",
            Decimal("1.250"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
            segment_key="usd_m_futures",
        ),
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-2",
    )

    assert handle.status == "accepted"
    method, params = client.calls[0]
    body = params[0]
    assert method == "execution_submit_intent"
    assert body["intent"]["target_quantity"] == "1.250"
    assert body["intent"]["strategy_id"] == "sma"
    assert body["intent"]["segment_key"] == "usd_m_futures"
    assert body["intent"]["algorithm"] == {"type": "immediate"}


def test_execution_client_encodes_explicit_arrival_benchmark() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)

    port.target_position(
        TargetPositionRequest(
            "BTCUSDT",
            Decimal("2"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
            execution_benchmarks=(
                ExecutionBenchmark(
                    instrument_id="BTCUSDT",
                    market_id="market:binance:spot:BTCUSDT",
                    price=Decimal("100.25"),
                    observed_at_unix_nanos=123,
                ),
            ),
        ),
        strategy_id="benchmark",
        instance_id="instance-1",
        request_id="request-benchmark",
    )

    intent = client.calls[0][1][0]["intent"]
    assert intent["execution_benchmarks"] == [
        {
            "kind": "arrival",
            "leg_id": None,
            "instrument_id": "BTCUSDT",
            "market_id": "market:binance:spot:BTCUSDT",
            "price": "100.25",
            "observed_at_unix_nanos": 123,
        }
    ]


def test_execution_benchmark_rejects_invalid_or_duplicate_leg_identity() -> None:
    with pytest.raises(ValueError, match="kind must be arrival"):
        ExecutionBenchmark(
            instrument_id="BTCUSDT",
            market_id="market:binance:spot:BTCUSDT",
            price=Decimal("100"),
            observed_at_unix_nanos=123,
            kind="limit_price",
        )

    benchmark = ExecutionBenchmark(
        instrument_id="BTCUSDT",
        market_id="market:binance:spot:BTCUSDT",
        price=Decimal("100"),
        observed_at_unix_nanos=123,
        leg_id="leg-0",
    )
    with pytest.raises(ValueError, match="leg_id values must be unique"):
        PairArbitrageRequest(
            ArbitrageLegRequest("BTCUSDT", "Buy", Decimal("1"), "main"),
            ArbitrageLegRequest("ETHUSDT", "Sell", Decimal("1"), "main"),
            algorithm=ImmediateAlgorithm(),
            execution_benchmarks=(benchmark, benchmark),
        )


def test_kairospy_encodes_twap_and_rejects_removed_algorithm_aliases() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)
    port.target_position(
        TargetPositionRequest(
            "BTCUSDT",
            Decimal("4"),
            algorithm=TwapAlgorithm(slice_count=2, slice_interval_nanos=10),
            account_id="main",
            source_event_time_unix_nanos=100,
        ),
        strategy_id="twap",
        instance_id="instance-1",
        request_id="request-twap",
    )
    intent = client.calls[0][1][0]["intent"]
    assert intent["algorithm"] == {
        "type": "twap",
        "policy": {"slice_count": 2, "slice_interval": 10},
    }

    with pytest.raises(TypeError):
        SplitOrderPolicy(child_count=2, **{"interval_millis": 10})
    with pytest.raises(TypeError):
        PairArbitrageRequest(
            ArbitrageLegRequest("BTCUSDT", "Buy", Decimal("1"), "main"),
            ArbitrageLegRequest("BTC-PERP", "Sell", Decimal("1"), "hedge"),
            **{"hedge_policy": HedgePolicy("leg-0", "leg-1")},
        )


def test_execution_client_forwards_canonical_agent_admission_evidence() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)
    original = TargetPositionRequest(
        "BTCUSDT",
        Decimal("2"),
        algorithm=ImmediateAlgorithm(),
        account_id="main",
        intent_id="intent-1",
    )
    effective = TargetPositionRequest(
        "BTCUSDT",
        Decimal("1"),
        algorithm=ImmediateAlgorithm(),
        account_id="main",
        intent_id="intent-1",
    )
    admission = IntentAdmissionEvidence(
        decision_id="decision-1",
        request_id="request-agent",
        intent_id="intent-1",
        source="decision_agent",
        outcome="revised",
        original_intent=original,
        effective_intent=effective,
    )

    port.target_position(
        effective,
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-agent",
        admission_evidence=admission,
    )

    body = client.calls[0][1][0]
    evidence = body["admission_evidence"]
    assert evidence["original_intent"]["target_quantity"] == "2"
    assert evidence["effective_intent"]["target_quantity"] == "1"
    for side in ("original", "effective"):
        encoded = json.dumps(
            evidence[f"{side}_intent"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        assert evidence[f"{side}_hash"] == hashlib.sha256(encoded).hexdigest()


def test_execution_client_applies_launch_live_safety_before_owner_command() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(
        client, allow_trading=False, require_limit_orders=True
    )

    handle = port.target_position(
        TargetPositionRequest(
            "BTCUSDT",
            Decimal("1"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
        ),
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-3",
    )

    assert handle.status == "rejected"
    assert "disabled" in (handle.error or "")
    assert client.calls == []


def test_execution_client_submits_typed_direct_order_without_exposing_connection() -> (
    None
):
    client = DirectOrderClient()
    port = ExecutionCommandClient(client)
    handle = port.submit_order(
        LimitOrderRequest(
            InstrumentId("instrument:test:SPY"),
            "main",
            OrderSide.BUY,
            Decimal("2"),
            Decimal("100.01"),
            time_in_force=TimeInForce.DAY,
            post_only=True,
            segment="equity",
        ),
        strategy_id="s",
        instance_id="i",
        request_id="order-request",
    )
    assert handle.status == "accepted"
    method, params = client.calls[0]
    body = params[0]
    assert method == "execution_submit_intent"
    intent = body["intent"]
    assert intent["strategy_id"] == "s"
    assert intent["legs"][0]["instrument_id"] == "instrument:test:SPY"
    assert intent["legs"][0]["quantity"] == "2"
    assert intent["legs"][0]["limit_price"] == "100.01"
    assert intent["legs"][0]["segment_key"] == "equity"
    assert intent["legs"][0]["options"]["post_only"] is True


def test_execution_client_cancels_replaces_and_scopes_bulk_cancel() -> None:
    client = DirectOrderClient()
    port = ExecutionCommandClient(client)
    canceled = port.cancel_order(
        "order-1",
        reason="test",
        strategy_id="s",
        instance_id="i",
        request_id="cancel-request",
    )
    replaced = port.replace_order(
        "order-1",
        ReplaceOrderRequest(limit_price=Decimal("101")),
        strategy_id="s",
        instance_id="i",
        request_id="replace-request",
    )
    bulk = port.cancel_all(
        instrument_id="instrument:test:SPY",
        account_id="main",
        reason="stop",
        strategy_id="s",
        instance_id="i",
        request_id="bulk-request",
    )
    assert canceled.status == replaced.status == "accepted"
    assert bulk.status == "rejected"
    assert any(method == "execution_replace_order" for method, _ in client.calls)


def test_execution_client_encodes_pair_arbitrage_as_two_execution_legs() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)
    handle = port.pair_arbitrage(
        PairArbitrageRequest(
            ArbitrageLegRequest(
                "BTCUSDT", "Buy", Decimal("1"), "binance-main", segment_key="spot"
            ),
            ArbitrageLegRequest(
                "BTC-PERP", "Sell", Decimal("1"), "okx-hedge", segment_key="perp"
            ),
            algorithm=ImmediateAlgorithm(),
        ),
        strategy_id="arb",
        instance_id="instance-1",
        request_id="request-pair",
    )
    assert handle.status == "accepted"
    intent = client.calls[0][1][0]["intent"]
    assert intent["intent_type"] == "PairArbitrage"
    assert [leg["side"] for leg in intent["legs"]] == ["Buy", "Sell"]


def test_pair_request_exposes_split_maker_and_hedge_controls() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)
    port.pair_arbitrage(
        PairArbitrageRequest(
            ArbitrageLegRequest(
                "USDCUSDT",
                "Buy",
                Decimal("100"),
                "maker-main",
                split=SplitOrderPolicy(child_count=4),
                maker=MakerExecutionPolicy(
                    max_inventory_abs=Decimal("200")
                ),
            ),
            ArbitrageLegRequest("USDCUSDT-PERP", "Sell", Decimal("100"), "maker-hedge"),
            algorithm=MakerTakerHedgeAlgorithm(
                HedgePolicy("leg-0", "leg-1", max_unhedged_quantity=Decimal("1"))
            ),
        ),
        strategy_id="maker",
        instance_id="instance-1",
        request_id="request-maker",
    )
    intent = client.calls[0][1][0]["intent"]
    assert intent["legs"][0]["options"]["split"]["child_count"] == 4
    assert intent["legs"][0]["options"]["maker"]["max_inventory_abs"] == "200"
    assert intent["algorithm"]["type"] == "maker_taker_hedge"
    assert intent["algorithm"]["policy"]["leader_leg_id"] == "leg-0"


def test_quote_provisioning_is_a_two_sided_execution_intent() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)
    handle = port.quote_provisioning(
        QuoteProvisioningRequest(
            "USDCUSDT",
            Decimal("0.9998"),
            Decimal("100"),
            Decimal("1.0002"),
            Decimal("100"),
            algorithm=PassiveLimitAlgorithm(
                reprice_interval_nanos=100_000_000,
                max_quote_age_nanos=500_000_000,
            ),
            account_id="main",
            maker=MakerExecutionPolicy(max_quote_age_millis=500),
        ),
        strategy_id="maker",
        instance_id="instance-1",
        request_id="request-quote",
    )
    assert handle.status == "accepted"
    intent = client.calls[0][1][0]["intent"]
    assert intent["intent_type"] == "QuoteProvisioning"
    assert intent["algorithm"] == {
        "type": "passive_limit",
        "policy": {
            "reprice_interval": 100_000_000,
            "max_quote_age": 500_000_000,
        },
    }
    assert [leg["side"] for leg in intent["legs"]] == ["Buy", "Sell"]


def test_quote_provisioning_rejects_the_removed_immediate_path() -> None:
    with pytest.raises(ValueError, match="requires PassiveLimit"):
        QuoteProvisioningRequest(
            "USDCUSDT",
            Decimal("0.9998"),
            Decimal("100"),
            Decimal("1.0002"),
            Decimal("100"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
        )


def test_quote_refresh_uses_execution_owner_replace_boundary() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)
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
    assert handle.status == "rejected"
    assert client.calls == []


def test_execution_client_encodes_portfolio_targets_as_target_position_legs() -> None:
    client = RecordingClient()
    port = ExecutionCommandClient(client)
    handle = port.portfolio_rebalance(
        PortfolioRebalanceRequest(
            (
                PortfolioRebalanceTarget("BTCUSDT", Decimal("2"), "main"),
                PortfolioRebalanceTarget("ETHUSDT", Decimal("5"), "secondary"),
            ),
            algorithm=ImmediateAlgorithm(),
        ),
        strategy_id="portfolio",
        instance_id="instance-1",
        request_id="request-portfolio",
    )
    assert handle.status == "accepted"
    intent = client.calls[0][1][0]["intent"]
    assert intent["intent_type"] == "PortfolioRebalance"
    assert all(leg["target_position"] for leg in intent["legs"])
    assert intent["account_ids"] == ["main", "secondary"]


def test_market_and_execution_command_ports_use_contract_control_clients(tmp_path) -> None:
    market = MarketCommandClient(
        MarketControlClient(tmp_path / "market.sock"), launch_id="launch-1"
    )
    execution = ExecutionCommandClient(
        ExecutionControlClient(tmp_path / "execution.sock"), launch_id="launch-1"
    )

    assert isinstance(market, MarketCommandClient)
    assert isinstance(execution, ExecutionCommandClient)
    assert market.launch_id == "launch-1"
    assert execution.launch_id == "launch-1"
