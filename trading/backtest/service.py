from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from trading.storage.codec import to_primitive

from trading.risk.limits import RiskLimits
from trading.strategies.bull_put_spread import BullPutSpreadConfig, BullPutSpreadStrategy
from trading.strategies.specs import bull_put_strategy_spec

from .engine import BacktestEngine
from .feed import HistoricalDataset
from .repository import BacktestRepository
from .result import BacktestConfig, BacktestResult


class BacktestService:
    def __init__(self, repository: BacktestRepository) -> None:
        self.repository = repository

    def run_suite(
        self,
        dataset: HistoricalDataset,
        config: BacktestConfig,
        strategy_config: BullPutSpreadConfig,
        risk_limits: RiskLimits,
    ) -> tuple[BacktestResult, BacktestResult]:
        results = []
        strategy_spec, execution_policy = bull_put_strategy_spec(strategy_config)
        for model in ("conservative", "stress"):
            model_config = replace(config, fill_model=model)
            result = BacktestEngine(dataset, model_config, BullPutSpreadStrategy(strategy_config), risk_limits).run()
            result.metrics["dataset_hash"] = dataset.manifest.content_hash
            result.metrics["code_version"] = dataset.manifest.code_version
            result.metrics["strategy_spec_hash"] = strategy_spec.spec_hash
            result.metrics["execution_policy_id"] = execution_policy.policy_id
            result.metrics["execution_policy_version"] = execution_policy.version
            self.repository.save(result, strategy_config=strategy_config, risk_limits=risk_limits)
            results.append(result)
        return results[0], results[1]

    def validate_splits(
        self,
        datasets: tuple[HistoricalDataset, HistoricalDataset, HistoricalDataset],
        config: BacktestConfig,
        strategy_config: BullPutSpreadConfig,
        risk_limits: RiskLimits,
    ) -> Path:
        expected = ("development", "validation", "test")
        actual = tuple(dataset.manifest.split for dataset in datasets)
        if actual != expected:
            raise ValueError(f"datasets must be ordered as {expected}, got {actual}")
        material = json.dumps({
            "datasets": [dataset.manifest.content_hash for dataset in datasets],
            "strategy": to_primitive(strategy_config),
            "risk": to_primitive(risk_limits),
            "config": to_primitive(config),
        }, sort_keys=True)
        experiment_id = uuid5(NAMESPACE_URL, material)
        rows = []
        for dataset in datasets:
            split_config = replace(config, start=dataset.manifest.start, end=dataset.manifest.end)
            conservative, stress = self.run_suite(dataset, split_config, strategy_config, risk_limits)
            for result in (conservative, stress):
                rows.append({
                    "split": dataset.manifest.split,
                    "dataset_id": dataset.manifest.dataset_id,
                    "synthetic": dataset.manifest.synthetic,
                    "model": result.config.fill_model,
                    "run_id": str(result.run_id),
                    "status": result.status.value,
                    "total_return": to_primitive(result.metrics["total_return"]),
                    "max_drawdown": to_primitive(result.metrics["max_drawdown"]),
                })
        directory = self.repository.root / "validations" / str(experiment_id)
        directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "experiment_id": str(experiment_id),
            "parameters_frozen": True,
            "datasets": [dataset.manifest.dataset_id for dataset in datasets],
            "synthetic": any(dataset.manifest.synthetic for dataset in datasets),
            "warning": "Synthetic split results validate workflow only, not out-of-sample performance." if any(dataset.manifest.synthetic for dataset in datasets) else "Historical out-of-sample results do not guarantee future performance.",
            "results": rows,
        }
        (directory / "validation-summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return directory
