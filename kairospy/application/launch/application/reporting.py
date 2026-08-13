"""Canonical backtest report and end-to-end correlation contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from typing import Any, Mapping

from ...data import DatasetSetRef
from .semantics import OptionBacktestConstraints


_REQUIRED_METRICS = frozenset(
    {
        "total_return",
        "annualized_return",
        "max_drawdown",
        "sharpe",
        "sortino",
        "realized_pnl",
        "unrealized_pnl",
        "fees",
        "slippage_cost",
        "spread_count",
        "win_rate",
        "average_win",
        "average_loss",
        "profit_factor",
        "premium_captured",
        "maximum_position_loss",
        "average_holding_period",
        "entry_dte",
        "exit_dte",
        "entry_delta",
        "exit_delta",
        "fill_rejections",
        "quote_missing",
        "risk_rejections",
    }
)


@dataclass(frozen=True, slots=True)
class BacktestReportContext:
    launch_id: str
    instance_id: str
    strategy_id: str
    normalized_config_hash: str
    dataset_set: DatasetSetRef
    seed: int
    code_version: str

    def __post_init__(self) -> None:
        for name in (
            "launch_id",
            "instance_id",
            "strategy_id",
            "normalized_config_hash",
            "code_version",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if self.seed < 0:
            raise ValueError("backtest seed cannot be negative")


@dataclass(frozen=True, slots=True)
class BacktestCorrelationTrace:
    decision_id: str
    market_event_id: str
    reference_snapshot_id: str
    intent_id: str
    risk_decision_id: str
    order_ids: tuple[str, ...]
    fill_ids: tuple[str, ...]
    account_transaction_ids: tuple[str, ...]
    position_id: str
    pnl_attribution_id: str

    def __post_init__(self) -> None:
        for name in (
            "decision_id",
            "market_event_id",
            "reference_snapshot_id",
            "intent_id",
            "risk_decision_id",
            "position_id",
            "pnl_attribution_id",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"trace {name} is required")
        for name in ("order_ids", "fill_ids", "account_transaction_ids"):
            values = tuple(getattr(self, name))
            if not values or any(not value.strip() for value in values):
                raise ValueError(f"trace {name} must contain non-empty identities")
            if len(values) != len(set(values)):
                raise ValueError(f"trace {name} cannot contain duplicates")
            object.__setattr__(self, name, values)


@dataclass(frozen=True, slots=True)
class CanonicalBacktestReportApplication:
    """Validate and assemble the report; business owners supply their metrics."""

    def build(
        self,
        context: BacktestReportContext,
        *,
        scenarios: Mapping[str, Mapping[str, Any]],
        traces: tuple[BacktestCorrelationTrace, ...],
        limitations: tuple[str, ...],
        option_constraints: OptionBacktestConstraints | None = None,
    ) -> dict[str, Any]:
        required_scenarios = {"baseline_conservative", "stress_costs"}
        if set(scenarios) != required_scenarios:
            raise ValueError(
                "canonical report requires baseline_conservative and stress_costs"
            )
        for scenario, metrics in scenarios.items():
            missing = sorted(_REQUIRED_METRICS - set(metrics))
            if missing:
                raise ValueError(
                    f"canonical report scenario {scenario} is missing metrics: "
                    + ", ".join(missing)
                )
        if not traces:
            raise ValueError(
                "canonical report requires at least one complete trade trace"
            )
        decision_ids = [trace.decision_id for trace in traces]
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError("canonical report decision traces must be unique")
        merged_limitations = tuple(
            dict.fromkeys(
                (
                    *(option_constraints.limitations if option_constraints else ()),
                    *limitations,
                )
            )
        )
        if not merged_limitations or any(
            not value.strip() for value in merged_limitations
        ):
            raise ValueError("canonical report must state its semantic limitations")
        deterministic = {
            "schema_version": 2,
            "strategy_id": context.strategy_id,
            "normalized_config_hash": context.normalized_config_hash,
            "dataset_set": context.dataset_set.as_dict(),
            "seed": context.seed,
            "code_version": context.code_version,
            "option_constraints": (
                option_constraints.as_dict() if option_constraints is not None else None
            ),
            "scenarios": {name: dict(scenarios[name]) for name in sorted(scenarios)},
            "traces": [asdict(trace) for trace in traces],
            "limitations": list(merged_limitations),
        }
        digest = hashlib.sha256(
            json.dumps(
                deterministic,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return {
            **deterministic,
            "launch_id": context.launch_id,
            "instance_id": context.instance_id,
            "deterministic_result_sha256": digest,
        }


__all__ = [
    "BacktestCorrelationTrace",
    "BacktestReportContext",
    "CanonicalBacktestReportApplication",
]
