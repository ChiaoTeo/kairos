from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from kairos.storage.codec import to_primitive

from kairos.risk.limits import RiskLimits
from kairos.strategies.bull_put_spread import BullPutSpreadConfig, BullPutSpreadStrategy
from kairos.strategies.specs import bull_put_strategy_spec

from .engine import BacktestEngine
from .feed import MarketReplayDataset, MarketSnapshotFeed
from .repository import BacktestRepository
from .result import BacktestConfig, BacktestResult


class BacktestExperimentRunner:
    def __init__(self, repository: BacktestRepository) -> None:
        self.repository = repository

    def run_suite(
        self,
        dataset: MarketReplayDataset | MarketSnapshotFeed,
        config: BacktestConfig,
        strategy_config: BullPutSpreadConfig,
        risk_limits: RiskLimits,
    ) -> tuple[BacktestResult, BacktestResult]:
        feed = dataset if hasattr(dataset, "between") else None
        historical = dataset.dataset if feed is not None else dataset
        results = []
        strategy_spec, execution_policy = bull_put_strategy_spec(strategy_config)
        for model in ("conservative", "stress"):
            model_config = replace(config, fill_model=model)
            result = BacktestEngine(feed or historical, model_config, BullPutSpreadStrategy(strategy_config), risk_limits).run()
            result.metrics["dataset_hash"] = historical.manifest.content_hash
            result.metrics["code_version"] = historical.manifest.code_version
            result.metrics["strategy_spec_hash"] = strategy_spec.spec_hash
            result.metrics["execution_policy_id"] = execution_policy.policy_id
            result.metrics["execution_policy_version"] = execution_policy.version
            self.repository.save(result, strategy_config=strategy_config, risk_limits=risk_limits)
            results.append(result)
        return results[0], results[1]

    def validate_splits(
        self,
        datasets: tuple[MarketReplayDataset | MarketSnapshotFeed, MarketReplayDataset | MarketSnapshotFeed,
                        MarketReplayDataset | MarketSnapshotFeed],
        config: BacktestConfig,
        strategy_config: BullPutSpreadConfig,
        risk_limits: RiskLimits,
    ) -> Path:
        sources = datasets
        historical = tuple(item.dataset if hasattr(item, "dataset") else item for item in sources)
        expected = ("development", "validation", "test")
        actual = tuple(dataset.manifest.split for dataset in historical)
        if actual != expected:
            raise ValueError(f"datasets must be ordered as {expected}, got {actual}")
        material = json.dumps({
            "datasets": [dataset.manifest.content_hash for dataset in historical],
            "strategy": to_primitive(strategy_config),
            "risk": to_primitive(risk_limits),
            "config": to_primitive(config),
        }, sort_keys=True)
        experiment_id = uuid5(NAMESPACE_URL, material)
        rows = []
        for source, dataset in zip(sources, historical):
            split_config = replace(config, start=dataset.manifest.start, end=dataset.manifest.end)
            conservative, stress = self.run_suite(source, split_config, strategy_config, risk_limits)
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
            "datasets": [dataset.manifest.dataset_id for dataset in historical],
            "synthetic": any(dataset.manifest.synthetic for dataset in historical),
            "warning": "Synthetic split results validate workflow only, not out-of-sample performance." if any(dataset.manifest.synthetic for dataset in historical) else "Historical out-of-sample results do not guarantee future performance.",
            "results": rows,
        }
        (directory / "validation-summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return directory


BacktestService = BacktestExperimentRunner
