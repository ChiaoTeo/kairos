from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from kairos.storage.codec import from_primitive, to_primitive

from .result import BacktestConfig, BacktestResult


class BacktestRepository:
    def __init__(self, root: str | Path = "data/backtests") -> None:
        self.root = Path(root)

    def run_dir(self, run_id: str | UUID, strategy_id: str | None = None) -> Path:
        if strategy_id:
            return self.root / strategy_id / str(run_id)
        matches = list(self.root.glob(f"*/{run_id}"))
        if len(matches) != 1:
            raise FileNotFoundError(f"backtest run not found or ambiguous: {run_id}")
        return matches[0]

    def save(self, result: BacktestResult, *, strategy_config: Any, risk_limits: Any) -> Path:
        directory = self.run_dir(result.run_id, result.strategy_id)
        directory.mkdir(parents=True, exist_ok=True)
        config = {
            "schema_version": 1,
            "backtest": to_primitive(result.config),
            "strategy": to_primitive(strategy_config),
            "risk": to_primitive(risk_limits),
        }
        self._json(directory / "config.json", config)
        self._jsonl(directory / "intents.jsonl", result.intents)
        self._jsonl(directory / "risk_decisions.jsonl", result.risk_decisions)
        self._jsonl(directory / "orders.jsonl", result.orders)
        self._jsonl(directory / "fills.jsonl", result.fills)
        self._jsonl(directory / "settlements.jsonl", result.settlements)
        self._jsonl(directory / "strategy_decisions.jsonl", result.strategy_decisions)
        self._positions_csv(directory / "positions.csv", result)
        self._equity_csv(directory / "equity.csv", result)
        self._trades_csv(directory / "trades.csv", result)
        self._json(directory / "metrics.json", {"schema_version": 1, "metrics": to_primitive(result.metrics)})
        summary = self._summary(result)
        (directory / "summary.md").write_text(summary, encoding="utf-8")
        audit_hash = self.audit_hash(directory)
        manifest = {
            "schema_version": 1,
            "run_id": str(result.run_id),
            "status": result.status.value,
            "strategy_id": result.strategy_id,
            "strategy_version": result.strategy_id.rsplit("-v", 1)[-1],
            "strategy_spec_hash": result.metrics.get("strategy_spec_hash"),
            "execution_policy_id": result.metrics.get("execution_policy_id"),
            "execution_policy_version": result.metrics.get("execution_policy_version"),
            "dataset_id": result.dataset_id,
            "dataset_hash": result.metrics.get("dataset_hash"),
            "code_version": result.metrics.get("code_version", "0.1.0"),
            "fill_model": result.config.fill_model,
            "commission_model": "fixed-v1",
            "risk_model": "limits-v1",
            "random_seed": result.config.random_seed,
            "sample_split": result.metrics.get("sample_split"),
            "synthetic_dataset": result.metrics.get("synthetic_dataset"),
            "validity_reasons": list(result.validity_reasons),
            "audit_hash": audit_hash,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "limitations": [
                "Synthetic data does not demonstrate strategy profitability." if result.metrics.get("synthetic_dataset") else "Historical results do not guarantee future performance.",
                "The fill model is an approximation and does not model exchange queue position.",
                "IBKR margin is approximated by defined-risk maximum loss.",
            ],
        }
        self._json(directory / "manifest.json", manifest)
        return directory

    def load_manifest(self, run_id: str | UUID) -> dict[str, Any]:
        return json.loads((self.run_dir(run_id) / "manifest.json").read_text())

    def load_metrics(self, run_id: str | UUID) -> dict[str, Any]:
        return json.loads((self.run_dir(run_id) / "metrics.json").read_text())["metrics"]

    def load_config(self, run_id: str | UUID) -> tuple[BacktestConfig, dict[str, Any], dict[str, Any]]:
        value = json.loads((self.run_dir(run_id) / "config.json").read_text())
        return from_primitive(value["backtest"], BacktestConfig), value["strategy"], value["risk"]

    @staticmethod
    def audit_hash(directory: Path) -> str:
        digest = sha256()
        for path in sorted(directory.iterdir()):
            if path.name == "manifest.json" or path.name.endswith(".tmp"):
                continue
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()

    @staticmethod
    def _json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _jsonl(path: Path, values) -> None:
        path.write_text("".join(json.dumps({"schema_version": 1, "data": to_primitive(value)}, sort_keys=True) + "\n" for value in values), encoding="utf-8")

    @staticmethod
    def _positions_csv(path: Path, result: BacktestResult) -> None:
        columns = ("timestamp", "instrument", "quantity", "average_price", "mark_mid", "mark_liquidation", "realized_pnl", "unrealized_pnl_mid", "mark_source")
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for snapshot in result.portfolio_snapshots:
                for position in snapshot.positions:
                    writer.writerow({"timestamp": snapshot.timestamp.isoformat(), "instrument": position.instrument_id.value, "quantity": position.quantity, "average_price": position.average_price, "mark_mid": position.mark_mid, "mark_liquidation": position.mark_liquidation, "realized_pnl": position.realized_pnl, "unrealized_pnl_mid": position.unrealized_pnl_mid, "mark_source": position.mark_source})

    @staticmethod
    def _equity_csv(path: Path, result: BacktestResult) -> None:
        columns = tuple(result.equity[0].__dataclass_fields__) if result.equity else ()
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for point in result.equity:
                row = asdict(point)
                row["timestamp"] = point.timestamp.isoformat()
                writer.writerow(row)

    @staticmethod
    def _trades_csv(path: Path, result: BacktestResult) -> None:
        columns = ("timestamp", "fill_id", "order_id", "structure_id", "net_price", "quantity", "commission", "slippage", "is_closing")
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for fill in result.fills:
                writer.writerow({"timestamp": fill.timestamp.isoformat(), "fill_id": fill.fill_id, "order_id": fill.order_id, "structure_id": fill.structure_id, "net_price": fill.net_price, "quantity": fill.quantity, "commission": fill.commission, "slippage": fill.slippage, "is_closing": fill.is_closing})

    @staticmethod
    def _summary(result: BacktestResult) -> str:
        metrics = result.metrics
        warning = "**Synthetic fixture: this result validates mechanics, not strategy effectiveness.**\n\n" if metrics.get("synthetic_dataset") else ""
        return (
            f"# Backtest {result.run_id}\n\n{warning}"
            f"- Status: {result.status.value}\n"
            f"- Strategy: {result.strategy_id}\n"
            f"- Dataset: {result.dataset_id} ({metrics.get('sample_split')})\n"
            f"- Fill model: {result.config.fill_model}\n"
            f"- Initial equity: {metrics.get('initial_equity')}\n"
            f"- Final equity: {metrics.get('final_equity')}\n"
            f"- Total return: {metrics.get('total_return')}\n"
            f"- Max drawdown: {metrics.get('max_drawdown')}\n"
            f"- Commissions: {metrics.get('commissions')}\n"
            f"- Slippage: {metrics.get('slippage')}\n\n"
            "The primary result must be interpreted together with the stress-model run and data-quality metrics.\n"
        )
