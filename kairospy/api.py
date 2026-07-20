from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from kairospy.configuration import DEFAULT_LAKE_ROOT
from kairospy.product_workflow import inspect_run, run_sma_backtest_workflow


@dataclass(frozen=True, slots=True)
class BacktestRequest:
    strategy: str
    dataset: str | None = None
    start: str | None = None
    end: str | None = None
    capital: Decimal = Decimal("100000")
    parameters: Mapping[str, object] = field(default_factory=dict)
    execution: str = "bar-close-conservative"
    execution_calibration: str | Path | None = None
    artifact_root: str | Path | None = None


class BacktestResultView:
    def __init__(self, payload: Mapping[str, object]) -> None:
        self.payload = dict(payload)

    def summary(self) -> dict[str, object]:
        keys = (
            "mode", "input_identity", "bars", "trades", "final_equity",
            "factor_hash", "decision_hash", "intent_hash", "audit_hash", "artifact",
        )
        return {key: self.payload[key] for key in keys if key in self.payload}

    def trades(self) -> dict[str, object]:
        return {
            "count": self.payload.get("trades", 0),
            "artifact": self.payload.get("artifact"),
        }

    def equity(self) -> dict[str, object]:
        return {
            "final_equity": self.payload.get("final_equity"),
            "audit_hash": self.payload.get("audit_hash"),
        }

    def explain(self, *, at: str | None = None) -> dict[str, object]:
        artifact = self.payload.get("artifact")
        if not artifact:
            raise ValueError("backtest result has no run artifact to explain")
        return inspect_run(SimpleNamespace(artifact=Path(str(artifact)), at=at, db=None))


class BacktestRunner:
    def __init__(self, lake_root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.lake_root = Path(lake_root)

    def run(self, request: BacktestRequest) -> BacktestResultView:
        strategy_id = _strategy_id(request.strategy)
        if strategy_id != "sma-cross-v1":
            raise ValueError(f"Kairos.backtest currently supports sma-cross-v1, got {request.strategy!r}")
        parameters = dict(request.parameters)
        args = SimpleNamespace(
            lake_root=self.lake_root,
            dataset=None if request.dataset == "fixture:sma-bars-v1" else request.dataset,
            fixture=request.dataset in (None, "fixture:sma-bars-v1"),
            start=request.start,
            end=request.end,
            fast=int(parameters.get("fast", 20)),
            slow=int(parameters.get("slow", 50)),
            initial_cash=Decimal(str(request.capital)),
            fee_bps=Decimal(str(parameters.get("fee_bps", 10))),
            artifact_root=Path(request.artifact_root) if request.artifact_root else None,
            execution=request.execution,
            execution_calibration=Path(request.execution_calibration) if request.execution_calibration else None,
        )
        return BacktestResultView(run_sma_backtest_workflow(args))


class Kairos:
    def __init__(self, lake_root: str | Path = DEFAULT_LAKE_ROOT) -> None:
        self.lake_root = Path(lake_root)

    def backtest(
        self, *, strategy: str, dataset: str | None = None, start: str | None = None,
        end: str | None = None, capital: int | Decimal = Decimal("100000"),
        parameters: Mapping[str, object] | None = None,
        execution: str = "bar-close-conservative",
        execution_calibration: str | Path | None = None,
        artifact_root: str | Path | None = None,
    ) -> BacktestResultView:
        request = BacktestRequest(
            strategy=strategy, dataset=dataset, start=start, end=end,
            capital=Decimal(str(capital)), parameters=parameters or {},
            execution=execution, execution_calibration=execution_calibration,
            artifact_root=artifact_root,
        )
        return BacktestRunner(self.lake_root).run(request)


def _strategy_id(value: str) -> str:
    return value.split("@", 1)[0]
