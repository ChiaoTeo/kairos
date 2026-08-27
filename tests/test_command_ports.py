from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from kairospy.infrastructure.contracts.execution import ExecutionControlClient
from kairospy.infrastructure.contracts.execution.types import (
    CancelOrderRequest,
    ExecutionIntentRequest,
    ReplaceOrderRequest as ContractReplaceOrderRequest,
    SubmitIntentRequest,
)
from kairospy.investment.apps.execution.application import IntentAdmissionEvidence
from kairospy.investment.apps.execution.application.commands import ExecutionCommandClient
from kairospy.strategy import (
    ArbitrageLegRequest,
    ExecutionBenchmark,
    HedgePolicy,
    ImmediateAlgorithm,
    InstrumentId,
    LimitOrderRequest,
    MakerExecutionPolicy,
    MakerTakerHedgeAlgorithm,
    OrderSide,
    PairArbitrageRequest,
    PassiveLimitAlgorithm,
    PortfolioRebalanceRequest,
    PortfolioRebalanceTarget,
    QuoteProvisioningRequest,
    ReplaceOrderRequest,
    SplitOrderPolicy,
    TargetPositionRequest,
    TimeInForce,
    TwapAlgorithm,
)


class _Status:
    status = "accepted"
    command_id = "command-1"
    intent_id = None
    order_id = None


class RecordingControl:
    def __init__(self) -> None:
        self.submissions: list[SubmitIntentRequest] = []
        self.cancels: list[tuple[str, CancelOrderRequest]] = []
        self.replacements: list[tuple[str, ContractReplaceOrderRequest]] = []

    def submit_intent(self, request: SubmitIntentRequest) -> _Status:
        self.submissions.append(request)
        return _Status()

    def cancel_order(self, order_id: str, request: CancelOrderRequest) -> _Status:
        self.cancels.append((order_id, request))
        return _Status()

    def replace_order(
        self, order_id: str, request: ContractReplaceOrderRequest
    ) -> _Status:
        self.replacements.append((order_id, request))
        return _Status()


def _port(control: RecordingControl | None = None, **policy: object):
    recorder = control or RecordingControl()
    return (
        ExecutionCommandClient(
            recorder,
            workspace_id="workspace:test",
            launch_id="launch-1",
            **policy,
        ),
        recorder,
    )


def _intent(control: RecordingControl) -> ExecutionIntentRequest:
    return control.submissions[-1].intent


def test_execution_control_client_is_owner_native(tmp_path: Path) -> None:
    client = ExecutionControlClient(tmp_path / "execution.sock")
    assert type(client).__module__ == "kairospy._native_execution_contract"


def test_target_position_maps_to_typed_owner_request() -> None:
    port, control = _port()
    handle = port.target_position(
        TargetPositionRequest(
            "instrument:test:BTCUSDT",
            Decimal("1.250"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
            segment_key="usd_m_futures",
        ),
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-2",
    )
    intent = _intent(control)
    assert handle.status == "accepted"
    assert type(control.submissions[0]).__module__ == "kairospy._native_execution_contract"
    assert intent.target_quantity == "1.25"
    assert intent.strategy_id == "sma"
    assert intent.segment_key == "usd_m_futures"
    assert intent.algorithm.kind == "immediate"


def test_execution_benchmark_is_native_and_read_only() -> None:
    port, control = _port()
    port.target_position(
        TargetPositionRequest(
            "instrument:test:BTCUSDT",
            Decimal("2"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
            execution_benchmarks=(
                ExecutionBenchmark(
                    instrument_id="instrument:test:BTCUSDT",
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
    benchmark = _intent(control).execution_benchmarks[0]
    assert benchmark.price == "100.25"
    assert benchmark.observed_at_unix_nanos == 123
    with pytest.raises(AttributeError):
        benchmark.price = "0"  # type: ignore[misc]


def test_twap_validation_and_native_algorithm_kind() -> None:
    port, control = _port()
    port.target_position(
        TargetPositionRequest(
            "instrument:test:BTCUSDT",
            Decimal("4"),
            algorithm=TwapAlgorithm(slice_count=2, slice_interval_nanos=10),
            account_id="main",
        ),
        strategy_id="twap",
        instance_id="instance-1",
        request_id="request-twap",
    )
    assert _intent(control).algorithm.kind == "twap"
    with pytest.raises(TypeError):
        SplitOrderPolicy(child_count=2, **{"interval_millis": 10})


def test_agent_admission_evidence_hashes_are_owned_by_native_contract() -> None:
    port, control = _port()
    original = TargetPositionRequest(
        "instrument:test:BTCUSDT",
        Decimal("2"),
        algorithm=ImmediateAlgorithm(),
        account_id="main",
        intent_id="intent-1",
    )
    effective = TargetPositionRequest(
        "instrument:test:BTCUSDT",
        Decimal("1"),
        algorithm=ImmediateAlgorithm(),
        account_id="main",
        intent_id="intent-1",
    )
    port.target_position(
        effective,
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-agent",
        admission_evidence=IntentAdmissionEvidence(
            decision_id="decision-1",
            request_id="request-agent",
            intent_id="intent-1",
            source="decision_agent",
            outcome="revised",
            original_intent=original,
            effective_intent=effective,
        ),
    )
    evidence = control.submissions[0].admission_evidence
    assert evidence is not None
    assert len(evidence.original_hash) == len(evidence.effective_hash) == 64
    assert evidence.original_hash != evidence.effective_hash


def test_launch_safety_rejects_before_owner_control() -> None:
    port, control = _port(allow_trading=False, require_limit_orders=True)
    handle = port.target_position(
        TargetPositionRequest(
            "instrument:test:BTCUSDT",
            Decimal("1"),
            algorithm=ImmediateAlgorithm(),
            account_id="main",
        ),
        strategy_id="sma",
        instance_id="instance-1",
        request_id="request-3",
    )
    assert handle.status == "rejected"
    assert control.submissions == []


def test_direct_order_is_one_typed_single_order_leg() -> None:
    port, control = _port()
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
        strategy_id="strategy-1",
        instance_id="instance-1",
        request_id="order-request",
    )
    intent = _intent(control)
    assert handle.status == "accepted"
    assert intent.intent_type == "single_order"
    assert intent.legs[0].instrument_id == "instrument:test:SPY"
    assert intent.legs[0].limit_price == "100.01"
    assert intent.legs[0].options.post_only is True


def test_cancel_and_replace_use_typed_contract_requests() -> None:
    port, control = _port()
    canceled = port.cancel_order(
        "order-1",
        reason="test",
        strategy_id="strategy-1",
        instance_id="instance-1",
        request_id="cancel-request",
    )
    replaced = port.replace_order(
        "order-1",
        ReplaceOrderRequest(limit_price=Decimal("101")),
        strategy_id="strategy-1",
        instance_id="instance-1",
        request_id="replace-request",
    )
    assert canceled.status == replaced.status == "accepted"
    assert type(control.cancels[0][1]).__module__ == "kairospy._native_execution_contract"
    assert type(control.replacements[0][1]).__module__ == "kairospy._native_execution_contract"


def test_pair_arbitrage_and_hedge_are_native_typed() -> None:
    port, control = _port()
    port.pair_arbitrage(
        PairArbitrageRequest(
            ArbitrageLegRequest(
                "instrument:test:USDCUSDT",
                "Buy",
                Decimal("100"),
                "maker-main",
                split=SplitOrderPolicy(child_count=4),
                maker=MakerExecutionPolicy(max_inventory_abs=Decimal("200")),
            ),
            ArbitrageLegRequest(
                "instrument:test:USDCUSDT-PERP",
                "Sell",
                Decimal("100"),
                "maker-hedge",
            ),
            algorithm=MakerTakerHedgeAlgorithm(
                HedgePolicy("leg-0", "leg-1", max_unhedged_quantity=Decimal("1"))
            ),
        ),
        strategy_id="maker",
        instance_id="instance-1",
        request_id="request-maker",
    )
    intent = _intent(control)
    assert intent.intent_type == "pair_arbitrage"
    assert [leg.side for leg in intent.legs] == ["buy", "sell"]
    assert intent.legs[0].options.has_split
    assert intent.legs[0].options.has_maker
    assert intent.algorithm.kind == "maker_taker_hedge"


def test_quote_and_portfolio_preserve_business_leg_semantics() -> None:
    port, control = _port()
    port.quote_provisioning(
        QuoteProvisioningRequest(
            "instrument:test:USDCUSDT",
            Decimal("0.9998"),
            Decimal("100"),
            Decimal("1.0002"),
            Decimal("100"),
            algorithm=PassiveLimitAlgorithm(100_000_000, 500_000_000),
            account_id="main",
        ),
        strategy_id="maker",
        instance_id="instance-1",
        request_id="request-quote",
    )
    quote = _intent(control)
    assert quote.algorithm.kind == "passive_limit"
    assert [leg.side for leg in quote.legs] == ["buy", "sell"]

    port.portfolio_rebalance(
        PortfolioRebalanceRequest(
            (
                PortfolioRebalanceTarget(
                    "instrument:test:BTCUSDT", Decimal("2"), "main"
                ),
                PortfolioRebalanceTarget(
                    "instrument:test:ETHUSDT", Decimal("5"), "secondary"
                ),
            ),
            algorithm=ImmediateAlgorithm(),
        ),
        strategy_id="portfolio",
        instance_id="instance-1",
        request_id="request-portfolio",
    )
    portfolio = _intent(control)
    assert all(leg.target_position for leg in portfolio.legs)
    assert portfolio.account_ids == ["main", "secondary"]


def test_execution_command_client_requires_full_runtime_scope(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workspace_id and launch_id"):
        ExecutionCommandClient(
            ExecutionControlClient(tmp_path / "execution.sock"),
            workspace_id="",
            launch_id="launch-1",
        )
