from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TRADING = ROOT / "kairospy" / "trading"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_trading_model_does_not_depend_on_upper_layers(self) -> None:
        forbidden = {
            "kairospy.accounting",
            "kairospy.connectors",
            "kairospy.backtest",
            "kairospy." + "catalog",
            "kairospy.data",
            "kairospy.execution",
            "kairospy.features",
            "kairospy.market_data",
            "kairospy.orchestration",
            "kairospy.pricing",
            "kairospy.capture",
            "kairospy.risk",
            "kairospy.storage",
            "kairospy.volatility",
        }
        violations: list[str] = []
        for path in sorted(TRADING.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: tuple[str, ...] = ()
                if isinstance(node, ast.Import):
                    names = tuple(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = (node.module,)
                for name in names:
                    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden):
                        violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {name}")
        self.assertEqual(violations, [], "trading model has upper-layer dependencies:\n" + "\n".join(violations))

    def test_strategy_runtime_contract_is_not_in_trading_model(self) -> None:
        self.assertFalse((TRADING / "strategy.py").exists())
        from datetime import datetime, timezone

        from kairospy.strategy import (
            StrategyContext,
            StrategyDecision,
            StrategyEvent,
            StrategyEventKind,
            StrategyProtocol,
        )

        self.assertEqual(StrategyContext.__module__, "kairospy.strategy.protocols")
        self.assertEqual(StrategyDecision.__module__, "kairospy.strategy.protocols")
        self.assertEqual(StrategyEvent.__module__, "kairospy.strategy.protocols")
        self.assertEqual(StrategyProtocol.__module__, "kairospy.strategy.protocols")
        event = StrategyEvent(StrategyEventKind.UNAVAILABLE, datetime(2026, 1, 1, tzinfo=timezone.utc))
        decision = StrategyDecision.none(timestamp=event.timestamp, reason="runtime unavailable")
        self.assertEqual(event.kind.value, "unavailable")
        self.assertEqual(decision.action, "none")

    def test_deleted_strategies_package_does_not_return(self) -> None:
        self.assertFalse((ROOT / "kairospy" / "strategies").exists())

    def test_json_ledger_repository_is_removed(self) -> None:
        self.assertFalse((ROOT / "kairospy" / "accounting" / "repository.py").exists())
        self.assertFalse((ROOT / "kairospy" / "application" / "ledger_migration.py").exists())

    def test_old_catalog_package_is_removed(self) -> None:
        self.assertFalse((ROOT / "kairospy" / "catalog").exists())
        forbidden = ("Instrument" + "Catalog", "ExternalMapping" + "Repository")
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                if name in text:
                    violations.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(violations, [], "old catalog code remains:\n" + "\n".join(violations))

    def test_only_current_reference_and_metadata_models_exist(self) -> None:
        self.assertFalse((TRADING / "instrument.py").exists())
        self.assertFalse((ROOT / "kairospy" / "data" / ("metadata_" + "migration.py")).exists())
        self.assertFalse((ROOT / ("re" + "search") / ("btc_study_" + "governance.py")).exists())

    def test_legacy_instrument_access_is_removed(self) -> None:
        forbidden = ("definition.product_" + "spec", "definition.listings" + "[", "definition.listing" + "(")
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "legacy instrument access remains:\n" + "\n".join(violations))

    def test_removed_dataset_and_surface_repositories_do_not_return(self) -> None:
        forbidden = ("DatasetRepository", "Re" + "search" + "DatasetStore", "SurfaceRepository")
        violations = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                if name in text:
                    violations.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(violations, [], "removed data repositories remain:\n" + "\n".join(violations))
        self.assertFalse((ROOT / "kairospy" / "volatility" / "repository.py").exists())


if __name__ == "__main__":
    unittest.main()
