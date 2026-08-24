from __future__ import annotations

from dataclasses import replace

import pytest

from kairospy import DatasetRef, DatasetSetRef
from kairospy.system.apps.launch.application import (
    BacktestCorrelationTrace,
    BacktestReportContext,
    CanonicalBacktestReportApplication,
    OptionBacktestConstraints,
)


def _dataset_set() -> DatasetSetRef:
    return DatasetSetRef(
        (
            DatasetRef(
                dataset_id="market.quote/SPY/options",
                version="v1",
                content_hash="a" * 64,
                owner="market",
                kind="quote",
                subject="SPY-options",
                start_time_unix_nanos=1,
                end_time_unix_nanos=2,
                event_count=2,
            ),
        )
    )


def _metrics() -> dict[str, object]:
    return {
        "total_return": "0.01",
        "annualized_return": "0.02",
        "max_drawdown": "0.03",
        "sharpe": "1",
        "sortino": "1.2",
        "realized_pnl": "10",
        "unrealized_pnl": "0",
        "fees": "1",
        "slippage_cost": "2",
        "spread_count": 1,
        "win_rate": "1",
        "average_win": "10",
        "average_loss": "0",
        "profit_factor": "10",
        "premium_captured": "12",
        "maximum_position_loss": "88",
        "average_holding_period": "7d",
        "entry_dte": "40",
        "exit_dte": "21",
        "entry_delta": "-0.25",
        "exit_delta": "-0.15",
        "fill_rejections": 0,
        "quote_missing": 0,
        "risk_rejections": 0,
    }


def _trace() -> BacktestCorrelationTrace:
    return BacktestCorrelationTrace(
        decision_id="decision-1",
        market_event_id="event-1",
        reference_snapshot_id="reference-1",
        intent_id="intent-1",
        risk_decision_id="risk-1",
        order_ids=("order-short", "order-long"),
        fill_ids=("fill-short", "fill-long"),
        account_transaction_ids=("transaction-short", "transaction-long"),
        position_id="position-1",
        pnl_attribution_id="pnl-1",
    )


def test_canonical_report_hash_excludes_runtime_instance_identity() -> None:
    context = BacktestReportContext(
        launch_id="backtest",
        instance_id="instance-a",
        strategy_id="spy-put-spread",
        normalized_config_hash="config-hash",
        dataset_set=_dataset_set(),
        seed=42,
        code_version="commit-1",
    )
    scenarios = {
        "baseline_conservative": _metrics(),
        "stress_costs": _metrics() | {"slippage_cost": "4"},
    }
    application = CanonicalBacktestReportApplication()

    first = application.build(
        context,
        scenarios=scenarios,
        traces=(_trace(),),
        limitations=("no expiry holding", "package fills only"),
    )
    second = application.build(
        replace(context, instance_id="instance-b"),
        scenarios=scenarios,
        traces=(_trace(),),
        limitations=("no expiry holding", "package fills only"),
    )

    assert first["instance_id"] != second["instance_id"]
    assert first["deterministic_result_sha256"] == second["deterministic_result_sha256"]
    assert first["dataset_set"]["composition_hash"] == _dataset_set().composition_hash


def test_canonical_report_rejects_incomplete_owner_metrics_or_trace() -> None:
    context = BacktestReportContext(
        launch_id="backtest",
        instance_id="instance",
        strategy_id="strategy",
        normalized_config_hash="hash",
        dataset_set=_dataset_set(),
        seed=42,
        code_version="commit",
    )
    with pytest.raises(ValueError, match="missing metrics"):
        CanonicalBacktestReportApplication().build(
            context,
            scenarios={"baseline_conservative": {}, "stress_costs": _metrics()},
            traces=(_trace(),),
            limitations=("no expiry",),
        )


def test_canonical_report_includes_launch_option_constraints_and_limitations() -> None:
    context = BacktestReportContext(
        launch_id="backtest",
        instance_id="instance",
        strategy_id="spy-put-spread",
        normalized_config_hash="hash",
        dataset_set=_dataset_set(),
        seed=42,
        code_version="commit",
    )
    constraints = OptionBacktestConstraints(exit_before_expiry_days=2)

    report = CanonicalBacktestReportApplication().build(
        context,
        scenarios={
            "baseline_conservative": _metrics(),
            "stress_costs": _metrics(),
        },
        traces=(_trace(),),
        limitations=(),
        option_constraints=constraints,
    )

    assert report["option_constraints"]["exit_before_expiry_days"] == 2
    assert any("assignment" in value for value in report["limitations"])
    assert any("package" in value for value in report["limitations"])
