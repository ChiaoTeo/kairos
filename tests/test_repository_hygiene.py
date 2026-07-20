from __future__ import annotations

from pathlib import Path
import re
import subprocess
import tomllib
import unittest


ROOT = Path(__file__).parents[1]
SECRET = re.compile(rb"(?:ma_[A-Za-z0-9]{24,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,})")


class RepositoryHygieneTests(unittest.TestCase):
    def test_local_runtime_artifacts_are_not_tracked(self):
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        forbidden = []
        for value in result.stdout.splitlines():
            parts = value.split("/")
            if (
                value.startswith("pyenv/")
                or value.startswith(".pytest_cache/")
                or "__pycache__" in parts
                or value.endswith(".pyc")
            ):
                forbidden.append(value)
        self.assertEqual(forbidden, [])

    def test_notebook_checkpoints_are_not_present(self):
        files = [path for path in ROOT.rglob("*") if path.is_file() and ".ipynb_checkpoints" in path.parts]
        self.assertEqual(files, [])

    def test_examples_and_docs_do_not_contain_common_live_secret_shapes(self):
        matches = []
        for root in (ROOT / "examples", ROOT / "docs", ROOT / "README.md"):
            paths = (root,) if root.is_file() else root.rglob("*")
            for path in paths:
                if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
                    continue
                if SECRET.search(path.read_bytes()):
                    matches.append(str(path.relative_to(ROOT)))
        self.assertEqual(matches, [])

    def test_studies_workspace_is_not_packaged(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = config["project"]["scripts"]
        package_find = config["tool"]["setuptools"]["packages"]["find"]
        includes = package_find["include"]
        excludes = package_find["exclude"]
        self.assertTrue((ROOT / "kairos").is_dir())
        self.assertFalse((ROOT / "kairos" / "research").exists())
        self.assertFalse((ROOT / "trading").exists())
        self.assertIn("kairos", scripts)
        self.assertNotIn("trader", scripts)
        self.assertIn("kairos*", includes)
        self.assertNotIn("trading*", includes)
        self.assertNotIn("research*", includes)
        self.assertNotIn("studies*", includes)
        self.assertIn("kairos.research*", excludes)
        self.assertIn("research*", excludes)
        self.assertIn("studies*", excludes)

    def test_source_workspace_study_commands_are_hidden_from_product_help(self):
        cli = (ROOT / "kairos" / "__main__.py").read_text(encoding="utf-8")
        required = (
            'data_actions.add_parser("btc-options-readiness", help=argparse.SUPPRESS)',
            'actions.add_parser("register-btc-iron-condor", help=argparse.SUPPRESS)',
            'actions.add_parser("readiness", help=argparse.SUPPRESS)',
        )
        for marker in required:
            self.assertIn(marker, cli)
        self.assertIn("is not included in the pip package", cli)

    def test_packaged_modules_do_not_import_workspace_research_at_module_load(self):
        offenders = []
        for root in (ROOT / "kairos",):
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if line == stripped and stripped.startswith(
                        ("from research ", "from research.", "import research", "from studies ", "from studies.", "import studies")
                    ):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_packaged_modules_do_not_include_legacy_backtest_mock_entrypoint(self):
        self.assertFalse((ROOT / "kairos" / "backtest" / "mock.py").exists())
        cli = (ROOT / "kairos" / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn('"mock"', cli)
        self.assertNotIn("backtest mock", cli)
        offenders = []
        for root in (ROOT / "kairos",):
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if (
                        stripped.startswith(("from kairos.backtest.mock", "import kairos.backtest.mock"))
                        or "MockScenario" in stripped
                        or "make_mock_dataset" in stripped
                        or "curated.mock" in stripped
                    ):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_deleted_legacy_product_names_do_not_return(self):
        deleted_paths = (
            ROOT / "kairos" / "backtest" / "mock.py",
            ROOT / "kairos" / "adapters",
            ROOT / "kairos" / "adapters" / "massive" / "day_aggs.py",
            ROOT / "kairos" / "adapters" / "massive" / "equity_day_aggs.py",
            ROOT / "kairos" / "adapters" / "massive" / "option_iv.py",
            ROOT / "kairos" / "adapters" / "massive" / "readiness.py",
            ROOT / "kairos" / "adapters" / "base.py",
            ROOT / "kairos" / "adapters" / "binance" / "adapter.py",
            ROOT / "kairos" / "adapters" / "ibkr" / "adapter.py",
            ROOT / "kairos" / "adapters" / "composite.py",
            ROOT / "kairos" / "backtest" / "service.py",
            ROOT / "kairos" / "data" / "health.py",
            ROOT / "kairos" / "features" / "us_equity_momentum_readiness.py",
            ROOT / "kairos" / "data" / "models.py",
            ROOT / "kairos" / "pricing" / "models.py",
            ROOT / "kairos" / "pricing" / "option_pricing_models.py",
            ROOT / "kairos" / "pricing" / "service.py",
            ROOT / "kairos" / "reference" / "models.py",
            ROOT / "kairos" / "research" / "validation" / "models.py",
            ROOT / "kairos" / "research" / "service.py",
            ROOT / "kairos" / "treasury" / "service.py",
            ROOT / "kairos" / "treasury" / "models.py",
            ROOT / "kairos" / "treasury" / "transfer_models.py",
            ROOT / "kairos" / "volatility" / "models.py",
            ROOT / "kairos" / "strategies" / "base.py",
            ROOT / "kairos" / "treasury" / "adapter.py",
            ROOT / "kairos" / "application" / "runtime_failure_matrix.py",
            ROOT / "kairos" / "application" / "runtime_golden.py",
            ROOT / "kairos" / "application" / "task_supervisor.py",
            ROOT / "kairos" / "backtest" / "golden.py",
            ROOT / "kairos" / "data" / "market_slice_curation.py",
            ROOT / "kairos" / "data" / "market_slice_storage.py",
            ROOT / "kairos" / "research" / "analyzer.py",
            ROOT / "kairos" / "research" / "selector.py",
            ROOT / "kairos" / "strategies" / "sma_cross.py",
            ROOT / "kairos" / "strategies" / "sma_strategy.py",
        )
        for path in deleted_paths:
            self.assertFalse(path.exists(), str(path.relative_to(ROOT)))

        forbidden = (
            "MockScenario",
            "make_mock_dataset",
            "curated.mock",
            "OptionDayIvPipeline",
            "MassiveReadiness",
            "DataHealth",
            "UsEquityMomentumReadiness",
            "TRADER_LAKE_ROOT",
            "AccountAdapter",
            "ExecutionAdapter",
            "MarketDataAdapter",
            "ReferenceAdapter",
            "ReferenceDataAdapter",
            "TransferAdapter",
            "CompositeMarketDataAdapter",
            "SimulatedExecutionAccountAdapter",
            "AsyncTaskSupervisor",
            "ManagedTaskSpec",
            "ManagedTaskStatus",
            "ManagedTaskSnapshot",
            "TaskCriticality",
            "TaskFault",
            "RuntimeGoldenResult",
            "run_runtime_golden",
            "GOLDEN_SCHEMA_VERSION",
            "GOLDEN_SCENARIO_ID",
            "FAILURE_MATRIX_ID",
            "run_runtime_failure_matrix",
            "MassiveSourceArchive",
            "MarketSliceCollectionPublisher",
            "MarketSliceFeed",
            "HistoricalFeed",
            "build_spxw_golden_pipeline",
            "ResearchSpec",
            "DatasetProductSpec",
            "DatasetProduct",
            "ProductSpec",
            "LIVE_PAPER",
            "live_paper_composition",
            "register_historical_dataset",
            "ReplaySliceFeed",
            "ManagedDataset",
            "ResearchRow",
            "ResearchResult",
        )
        forbidden_exact_names = re.compile(r"\b(BacktestService|ValuationService|ResearchService|TreasuryService)\b")
        offenders = []
        for root in (ROOT / "kairos", ROOT / "tests", ROOT / "examples"):
            for path in root.rglob("*.py"):
                if path == ROOT / "tests" / "test_repository_hygiene.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    if marker in text:
                        offenders.append(f"{path.relative_to(ROOT)} contains {marker}")
                if forbidden_exact_names.search(text):
                    offenders.append(f"{path.relative_to(ROOT)} contains a deleted generic service name")
        self.assertEqual(offenders, [])

    def test_packaged_modules_do_not_import_legacy_adapter_namespace(self):
        self.assertFalse((ROOT / "kairos" / "adapters").exists())
        offenders = []
        for root in (ROOT / "kairos", ROOT / "tests", ROOT / "examples"):
            for path in root.rglob("*.py"):
                if path == ROOT / "tests" / "test_repository_hygiene.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if stripped.startswith(("from kairos.adapters", "import kairos.adapters")):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_kairos_namespace_exposes_connectors_not_adapters(self):
        kairos_init = (ROOT / "kairos" / "__init__.py").read_text(encoding="utf-8")
        data_init = (ROOT / "kairos" / "data" / "__init__.py").read_text(encoding="utf-8")
        domain_init = (ROOT / "kairos" / "domain" / "__init__.py").read_text(encoding="utf-8")
        application_init = (ROOT / "kairos" / "application" / "__init__.py").read_text(encoding="utf-8")
        pricing_init = (ROOT / "kairos" / "pricing" / "__init__.py").read_text(encoding="utf-8")
        treasury_init = (ROOT / "kairos" / "treasury" / "__init__.py").read_text(encoding="utf-8")
        research_platform_init = (ROOT / "kairos" / "research_platform" / "__init__.py").read_text(encoding="utf-8")
        self.assertTrue((ROOT / "kairos" / "connectors" / "__init__.py").exists())
        self.assertFalse((ROOT / "kairos" / "research").exists())
        self.assertNotIn('"adapters"', kairos_init)
        self.assertNotIn('"task_supervisor"', kairos_init)
        self.assertNotIn('"runtime_golden"', kairos_init)
        self.assertNotIn('"runtime_failure_matrix"', kairos_init)
        self.assertNotIn('"market_slice_storage"', kairos_init)
        self.assertNotIn('"market_slice_curation"', kairos_init)
        self.assertNotIn('"research"', kairos_init)
        self.assertIn('"research_platform"', kairos_init)
        self.assertNotIn('"Trader"', kairos_init)
        self.assertNotIn("TradingApplication", application_init)
        self.assertNotIn("AsyncTradingRuntime", application_init)
        self.assertIn("KairosApplication", application_init)
        self.assertIn("AsyncKairosRuntime", application_init)
        self.assertNotIn('"DatasetProduct"', data_init)
        self.assertNotIn('"DatasetProductSpec"', data_init)
        self.assertNotIn('"ProductSpec"', domain_init)
        self.assertNotIn("live_paper_composition", application_init)
        self.assertIn("paper_trading_composition", application_init)
        self.assertNotIn('"ValuationService"', pricing_init)
        self.assertNotIn('"TreasuryService"', treasury_init)
        self.assertNotIn('"ResearchSpec"', research_platform_init)
        self.assertNotIn('"service"', research_platform_init)
        self.assertNotIn('"analyzer"', research_platform_init)
        self.assertNotIn('"selector"', research_platform_init)
        self.assertIn('"option_capture"', research_platform_init)
        self.assertIn('"option_snapshot_analysis"', research_platform_init)
        self.assertIn('"option_universe_selector"', research_platform_init)
        for path in (
            ROOT / "kairos" / "connectors" / "__init__.py",
            ROOT / "kairos" / "connectors" / "__init__.py",
        ):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn('"adapter"', text)
            self.assertNotIn('"base"', text)
            self.assertNotIn('"composite"', text)
            self.assertNotIn('"day_aggs"', text)
            self.assertNotIn('"equity_day_aggs"', text)
            self.assertNotIn('"option_iv"', text)
            self.assertNotIn('"readiness"', text)
            self.assertNotIn("DayAgg", text)
            self.assertNotIn("DayIv", text)
            self.assertIn('"market_data_router"', text)
        massive_init = (ROOT / "kairos" / "connectors" / "massive" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("DayAgg", massive_init)
        self.assertNotIn("DayIv", massive_init)
        self.assertNotIn("Readiness", massive_init)
        for path in (ROOT / "kairos" / "connectors" / "massive").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("DayAgg", text)
            self.assertNotIn("DayIv", text)
            self.assertNotIn("Readiness", text)
        self.assertFalse((ROOT / "kairos" / "connectors" / "massive" / "day_aggs.py").exists())
        self.assertFalse((ROOT / "kairos" / "connectors" / "massive" / "equity_day_aggs.py").exists())
        self.assertFalse((ROOT / "kairos" / "connectors" / "massive" / "option_iv.py").exists())
        self.assertFalse((ROOT / "kairos" / "connectors" / "massive" / "readiness.py").exists())

    def test_new_data_contract_imports_do_not_use_models_module(self):
        offenders = []
        legacy_imports = (
            "from kairos.data.models",
            "import kairos.data.models",
            "from kairos.treasury.transfer_models",
            "import kairos.treasury.transfer_models",
            "from kairos.volatility.models",
            "import kairos.volatility.models",
            "from kairos.research_platform.validation.models",
            "import kairos.research_platform.validation.models",
            "from kairos.reference.models",
            "import kairos.reference.models",
            "from kairos.pricing.models",
            "import kairos.pricing.models",
            "from kairos.pricing.option_pricing_models",
            "import kairos.pricing.option_pricing_models",
            "from kairos.treasury.models",
            "import kairos.treasury.models",
        )
        roots = (ROOT / "kairos", ROOT / "tests", ROOT / "examples")
        for root in roots:
            for path in root.rglob("*.py"):
                if path == ROOT / "tests" / "test_repository_hygiene.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if stripped.startswith(legacy_imports):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_new_code_does_not_import_legacy_connector_base_or_service_modules(self):
        offenders = []
        legacy_imports = (
            "from kairos.connectors.base",
            "import kairos.connectors.base",
            "from kairos.strategies.base",
            "import kairos.strategies.base",
        )
        roots = (ROOT / "kairos", ROOT / "tests", ROOT / "examples")
        for root in roots:
            for path in root.rglob("*.py"):
                if path == ROOT / "tests" / "test_repository_hygiene.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if stripped.startswith(legacy_imports):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_runtime_boundaries_use_gateway_and_connector_language(self):
        allowed = {
            ROOT / "kairos" / "__main__.py",
            ROOT / "kairos" / "product_surface.py",
            ROOT / "kairos" / "connectors" / "binance" / "funding_ingestion.py",
            ROOT / "tests" / "test_naming_migration.py",
            ROOT / "tests" / "test_repository_hygiene.py",
        }
        offenders = []
        forbidden = (
            "account_adapter",
            "account_adapters",
            "no market-data adapter",
            "no execution adapter",
            "execution adapter does not support",
            "adapter does not support",
            "requires an account adapter",
        )
        for root in (ROOT / "kairos", ROOT / "tests"):
            for path in root.rglob("*.py"):
                if path in allowed:
                    continue
                text = path.read_text(encoding="utf-8")
                for token in forbidden:
                    if token in text:
                        offenders.append(f"{path.relative_to(ROOT)} contains {token}")
        self.assertEqual(offenders, [])

    def test_deleted_adapter_aggregation_modules_do_not_return(self):
        deleted_paths = (
            ROOT / "kairos" / "connectors" / "binance" / "adapter.py",
            ROOT / "kairos" / "connectors" / "ibkr" / "adapter.py",
            ROOT / "kairos" / "connectors" / "composite.py",
            ROOT / "kairos" / "treasury" / "adapter.py",
        )
        for path in deleted_paths:
            self.assertFalse(path.exists(), str(path.relative_to(ROOT)))

        offenders = []
        forbidden_imports = (
            "from kairos.connectors.binance.adapter",
            "import kairos.connectors.binance.adapter",
            "from kairos.connectors.ibkr.adapter",
            "import kairos.connectors.ibkr.adapter",
            "from kairos.connectors.composite",
            "import kairos.connectors.composite",
            "from kairos.treasury.adapter",
            "import kairos.treasury.adapter",
        )
        roots = (ROOT / "kairos", ROOT / "tests", ROOT / "examples")
        for root in roots:
            for path in root.rglob("*.py"):
                if path == ROOT / "tests" / "test_repository_hygiene.py":
                    continue
                for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    stripped = line.strip()
                    if stripped.startswith(forbidden_imports):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_transfer_package_public_api_uses_gateway_names(self):
        text = (ROOT / "kairos" / "connectors" / "transfer" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("BinanceTransferGateway", text)
        self.assertIn("BankTransferGateway", text)
        self.assertNotIn("BinanceTransferAdapter", text)
        self.assertNotIn("BankTransferAdapter", text)

    def test_ports_package_public_api_uses_port_names(self):
        text = (ROOT / "kairos" / "ports" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("ReferenceDataPort", text)
        self.assertIn("ExecutionPort", text)
        self.assertNotIn("Adapter", text)

    def test_connector_package_public_api_does_not_reexport_adapter_aliases(self):
        packages = (
            ROOT / "kairos" / "connectors" / "binance" / "__init__.py",
            ROOT / "kairos" / "connectors" / "ibkr" / "__init__.py",
            ROOT / "kairos" / "connectors" / "transfer" / "__init__.py",
        )
        offenders = []
        for path in packages:
            text = path.read_text(encoding="utf-8")
            if "Adapter" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_new_test_modules_use_connector_boundary_names(self):
        offenders = [path.name for path in (ROOT / "tests").glob("*adapter*.py")]
        self.assertEqual(offenders, [])

    def test_top_level_test_modules_avoid_generic_boundary_names(self):
        forbidden_names = {
            "test_service.py",
            "test_mock.py",
            "test_adapter.py",
            "test_backtest_models.py",
            "test_backtest_fill_models.py",
            "test_trader_api.py",
            "test_dataset_product_spec.py",
            "test_massive_readiness.py",
            "test_runtime_failure_matrix.py",
            "test_runtime_golden.py",
        }
        offenders = [path.name for path in (ROOT / "tests").glob("test_*.py") if path.name in forbidden_names]
        self.assertEqual(offenders, [])

    def test_examples_use_connector_directory_names(self):
        self.assertFalse((ROOT / "examples" / "adapters").exists())
        self.assertTrue((ROOT / "examples" / "connectors" / "reference_connector").exists())

    def test_user_facing_docs_use_connector_language(self):
        docs = (
            ROOT / "README.md",
            ROOT / "examples" / "README.md",
            ROOT / "examples" / "connectors" / "reference_connector" / "README.md",
            ROOT / "docs" / "architecture.md",
            ROOT / "docs" / "system_convergence_progress.md",
            ROOT / "docs" / "system_architecture_convergence_blueprint.md",
            ROOT / "docs" / "research_strategy_backtest_live_convergence_plan.md",
        )
        offenders = []
        for path in docs:
            text = path.read_text(encoding="utf-8")
            if "Adapter" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_product_docs_use_kairos_cli_name(self):
        docs = [ROOT / "README.md"]
        docs.extend(
            path
            for path in (ROOT / "docs").glob("*.md")
            if path.name != "naming_audit.md"
        )
        docs.extend((ROOT / "examples").rglob("*.md"))
        offenders = []
        for path in docs:
            text = path.read_text(encoding="utf-8")
            if "# Trader" in text or re.search(r"\bTrader\b", text) or re.search(r"\btrader\s+", text):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_user_facing_examples_and_guides_import_kairos_namespace(self):
        roots = [
            ROOT / "README.md",
            ROOT / "docs" / "data_layout.md",
            ROOT / "docs" / "data_usage_product_design.md",
            ROOT / "docs" / "research_data_guide.md",
            ROOT / "docs" / "tutorial_first_research.md",
            ROOT / "examples",
        ]
        offenders = []
        for root in roots:
            paths = (root,) if root.is_file() else root.rglob("*")
            for path in paths:
                if not path.is_file() or path.suffix not in {".py", ".md", ".ipynb"}:
                    continue
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip().lstrip('"')
                    if stripped.startswith(("from trading", "import trading")) or "trading." in stripped:
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_massive_entitlement_diagnostics_is_the_public_cli_name(self):
        cli = (ROOT / "kairos" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn('"massive-entitlement-diagnostics"', cli)
        self.assertNotIn('"massive-readiness"', cli)
        self.assertFalse((ROOT / "kairos" / "connectors" / "massive" / "readiness.py").exists())
        docs = [
            path
            for path in (ROOT / "docs").glob("*.md")
            if path.name != "naming_audit.md"
        ]
        docs.extend((ROOT / "examples").rglob("*.md"))
        offenders = []
        for path in docs:
            text = path.read_text(encoding="utf-8")
            if "massive-readiness" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_us_equity_momentum_diagnostics_is_the_public_data_cli_name(self):
        cli = (ROOT / "kairos" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn('"us-equity-momentum-diagnostics"', cli)
        self.assertNotIn('"us-equity-momentum-readiness"', cli)
        self.assertFalse((ROOT / "kairos" / "features" / "us_equity_momentum_readiness.py").exists())
        offenders = []
        for root in (ROOT / "kairos", ROOT / "tests", ROOT / "examples"):
            for path in root.rglob("*.py"):
                if path in {
                    ROOT / "tests" / "test_repository_hygiene.py",
                }:
                    continue
                text = path.read_text(encoding="utf-8")
                if "us_equity_momentum_readiness" in text or "UsEquityMomentumReadiness" in text:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_runtime_reference_and_failure_policy_are_the_public_cli_names(self):
        cli = (ROOT / "kairos" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn('"reference-artifact"', cli)
        self.assertIn('"failure-policy"', cli)
        self.assertIn('"soak"', cli)
        self.assertNotIn('"golden"', cli)
        self.assertNotIn('"failure-matrix"', cli)
        self.assertNotIn('add_parser("trade"', cli)
        self.assertNotIn('args.group == "trade"', cli)
        self.assertFalse((ROOT / "kairos" / "application" / "runtime_golden.py").exists())
        self.assertFalse((ROOT / "kairos" / "application" / "runtime_failure_matrix.py").exists())

    def test_spxw_reference_scenario_is_the_public_backtest_cli_name(self):
        cli = (ROOT / "kairos" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn('"spxw-reference-scenario"', cli)
        self.assertNotIn('"golden-spxw"', cli)
        self.assertTrue((ROOT / "kairos" / "backtest" / "spxw_reference_pipeline.py").exists())
        self.assertFalse((ROOT / "kairos" / "backtest" / "golden.py").exists())
        offenders = []
        for root in (ROOT / "kairos", ROOT / "tests", ROOT / "examples"):
            for path in root.rglob("*.py"):
                if path in {
                    ROOT / "tests" / "test_repository_hygiene.py",
                }:
                    continue
                text = path.read_text(encoding="utf-8")
                if "build_spxw_golden_pipeline" in text or "kairos.backtest.golden" in text:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_synthetic_scenarios_use_market_snapshot_contract_names(self):
        text = (ROOT / "kairos" / "backtest" / "synthetic_scenarios.py").read_text(encoding="utf-8")
        self.assertIn("MarketReplayDataset", text)
        self.assertIn("MarketSnapshot", text)
        self.assertIn("InstrumentLifecycleSnapshot", text)
        self.assertNotIn("HistoricalDataset", text)
        self.assertNotIn("MarketSlice", text)
        self.assertNotIn("ContractMetadata", text)


if __name__ == "__main__":
    unittest.main()
