from __future__ import annotations

import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = ROOT / "trading" / "domain"


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_domain_does_not_depend_on_upper_layers(self) -> None:
        forbidden = {
            "trading.accounting",
            "trading.adapters",
            "trading.backtest",
            "trading." + "catalog",
            "trading.data",
            "trading.execution",
            "trading.features",
            "trading.market_data",
            "trading.orchestration",
            "trading.pricing",
            "trading.research",
            "trading.risk",
            "trading.storage",
            "trading.strategies",
            "trading.volatility",
        }
        violations: list[str] = []
        for path in sorted(DOMAIN.glob("*.py")):
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
        self.assertEqual(violations, [], "domain has upper-layer dependencies:\n" + "\n".join(violations))

    def test_strategy_runtime_contract_is_not_in_domain(self) -> None:
        self.assertFalse((DOMAIN / "strategy.py").exists())
        from trading.strategies.base import StrategyContext, StrategyDecision

        self.assertEqual(StrategyContext.__module__, "trading.strategies.base")
        self.assertEqual(StrategyDecision.__module__, "trading.strategies.base")

    def test_strategies_do_not_depend_on_removed_history_module(self) -> None:
        violations = []
        for path in sorted((ROOT / "trading" / "strategies").glob("*.py")):
            if "trading.history" in path.read_text(encoding="utf-8"):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_json_ledger_repository_is_removed(self) -> None:
        self.assertFalse((ROOT / "trading" / "accounting" / "repository.py").exists())
        self.assertFalse((ROOT / "trading" / "application" / "ledger_migration.py").exists())

    def test_old_catalog_package_is_removed(self) -> None:
        self.assertFalse((ROOT / "trading" / "catalog").exists())
        forbidden = ("Instrument" + "Catalog", "ExternalMapping" + "Repository")
        violations = []
        for path in sorted((ROOT / "trading").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                if name in text:
                    violations.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(violations, [], "old catalog code remains:\n" + "\n".join(violations))

    def test_only_current_reference_and_metadata_models_exist(self) -> None:
        self.assertFalse((DOMAIN / "instrument.py").exists())
        self.assertFalse((ROOT / "trading" / "data" / ("metadata_" + "migration.py")).exists())
        self.assertFalse((ROOT / "research" / ("btc_study_" + "governance.py")).exists())

    def test_legacy_instrument_access_is_removed(self) -> None:
        forbidden = ("definition.product_" + "spec", "definition.listings" + "[", "definition.listing" + "(")
        violations = []
        for path in sorted((ROOT / "trading").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    violations.append(f"{path.relative_to(ROOT)}: {token}")
        self.assertEqual(violations, [], "legacy instrument access remains:\n" + "\n".join(violations))

    def test_removed_dataset_and_surface_repositories_do_not_return(self) -> None:
        forbidden = ("DatasetRepository", "ResearchDatasetStore", "SurfaceRepository")
        violations = []
        for path in sorted((ROOT / "trading").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for name in forbidden:
                if name in text:
                    violations.append(f"{path.relative_to(ROOT)}: {name}")
        self.assertEqual(violations, [], "removed data repositories remain:\n" + "\n".join(violations))
        self.assertFalse((ROOT / "trading" / "volatility" / "repository.py").exists())


if __name__ == "__main__":
    unittest.main()
