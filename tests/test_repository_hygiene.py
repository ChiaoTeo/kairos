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

    def test_user_examples_do_not_import_deleted_top_level_packages(self):
        forbidden_imports = (
            "from kairospy.accounting",
            "import kairospy.accounting",
            "from kairospy.application",
            "import kairospy.application",
            "from kairospy.backtest",
            "import kairospy.backtest",
            "from kairospy.capture",
            "import kairospy.capture",
            "from kairospy.connectors",
            "import kairospy.connectors",
            "from kairospy.contracts",
            "import kairospy.contracts",
            "from kairospy.features",
            "import kairospy.features",
            "from kairospy.lifecycle",
            "import kairospy.lifecycle",
            "from kairospy.market_data",
            "import kairospy.market_data",
            "from kairospy.orchestration",
            "import kairospy.orchestration",
            "from kairospy.ports",
            "import kairospy.ports",
            "from kairospy.pricing",
            "import kairospy.pricing",
            "from kairospy.storage",
            "import kairospy.storage",
            "from kairospy.trading",
            "import kairospy.trading",
            "from kairospy.treasury",
            "import kairospy.treasury",
            "from kairospy.validation",
            "import kairospy.validation",
            "from kairospy.volatility",
            "import kairospy.volatility",
        )
        offenders = []
        for path in sorted(ROOT.joinpath("examples").rglob("*")):
            if path.suffix not in {".py", ".ipynb", ".md"}:
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden_imports:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)} contains {marker}")
        self.assertEqual(offenders, [])

    def test_project_state_has_no_legacy_workspace_data_roots(self):
        for name in ("studies", "strategies", "workspaces", "study-workspaces", "study-candidates"):
            self.assertFalse((ROOT / ".kairos" / "data" / name).exists())

    def test_gitignore_does_not_hide_user_code_directories(self):
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertNotIn("\nstudies/\n", f"\n{gitignore}\n")
        self.assertNotIn("\nstrategies/\n", f"\n{gitignore}\n")

    def test_workspace_file_names_do_not_reintroduce_legacy_or_generic_terms(self):
        ignored_parts = {".git", "pyenv", ".venv", ".pytest_cache"}
        forbidden_exact = {
            "adapters",
            "base.py",
            "handler.py",
            "helpers.py",
            "manager.py",
            "models.py",
            "service.py",
            "trader",
            "utils.py",
        }
        forbidden_fragments = ("adapter",)
        allowed = set()
        offenders = []
        stack = [ROOT]
        while stack:
            current = stack.pop()
            for path in current.iterdir():
                relative = path.relative_to(ROOT)
                if path.is_dir() and path.name in ignored_parts:
                    continue
                if relative in allowed:
                    continue
                name = path.name
                if name in forbidden_exact or any(fragment in name for fragment in forbidden_fragments):
                    offenders.append(str(relative))
                if path.is_dir() and not path.is_symlink():
                    stack.append(path)
        self.assertEqual(offenders, [])

    def test_readme_core_naming_table_has_unique_rows(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| `([^`]+)` \|", readme, flags=re.MULTILINE)
        duplicates = sorted({name for name in rows if rows.count(name) > 1})
        allowed = {"DataProductDefinition"}
        offenders = [name for name in duplicates if name not in allowed]
        self.assertEqual(offenders, [])

    def test_readme_separates_user_install_from_source_workspace_development(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        required = (
            "普通用户不需要复制本仓库",
            "python3 -m pip install kairospy",
            "mkdir my-kairospy-project",
            "kairospy init",
            "kairospy workspace create alpha",
            "安装包只包含 Kairos 产品库和 CLI",
            "如果你是从源码参与开发，再使用 editable 安装",
            "./pyenv/bin/pip install -e",
        )
        for marker in required:
            self.assertIn(marker, readme)
        self.assertLess(readme.index("python3 -m pip install kairospy"), readme.index("./pyenv/bin/pip install -e"))
        self.assertNotIn("pip install kairospy\nmkdir", readme)
        self.assertNotIn("pip install trader", readme)
        self.assertNotIn("trader init", readme)

    def test_generated_project_metadata_uses_portable_root(self):
        metadata = (ROOT / ".kairos" / "project.json").read_text(encoding="utf-8")
        self.assertIn('"name": "kairospy"', metadata)
        self.assertIn('"root": "."', metadata)
        self.assertNotIn(str(ROOT), metadata)
        self.assertNotIn('"name": "trader"', metadata)

    def test_root_kairospy_toml_does_not_use_study_section(self):
        config = (ROOT / "kairos.toml").read_text(encoding="utf-8")
        self.assertIn('name = "kairospy"', config)
        self.assertNotIn("[study]", config)
        self.assertNotIn("[research]", config)
        self.assertNotIn('name = "trader"', config)

    def test_package_public_namespace_does_not_export_legacy_project_names(self):
        packages = (
            ROOT / "kairospy" / "__init__.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "__init__.py",
            ROOT / "kairospy" / "integrations" / "ports" / "__init__.py",
            ROOT / "kairospy" / "research" / "capture" / "__init__.py",
        )
        offenders = []
        forbidden = ('"adapters"', '"research"', '"trader"', '"trading"', "Adapter", "ResearchSpec", "ResearchService")
        for path in packages:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path.relative_to(ROOT)} contains {token}")
        self.assertEqual(offenders, [])

    def test_strategies_package_is_removed_in_favor_of_strategy_protocol(self):
        self.assertFalse((ROOT / "kairospy" / "strategies").exists())
        self.assertTrue((ROOT / "kairospy" / "strategy").exists())
        source = (ROOT / "kairospy" / "strategy" / "__init__.py").read_text(encoding="utf-8")
        for marker in ("Strategy", "Context", "StrategyDecision", "GovernedStrategyRuntime"):
            self.assertIn(marker, source)

    def test_core_code_does_not_import_deleted_strategies_package(self):
        offenders = []
        for path in sorted((ROOT / "kairospy").rglob("*.py")):
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith(("from kairospy.strategies", "import kairospy.strategies")):
                    offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_examples_and_tests_do_not_use_deleted_workspace_or_strategy_apis(self):
        forbidden = (
            "from kairospy.strategies",
            "import kairospy.strategies",
            "from kairospy.product_workflow",
            "import kairospy.product_workflow",
            "from kairospy.research.capture.session",
            "open_study",
            "StudyWorkspace",
            "BacktestRequest",
            "BacktestRunner",
            "from kairospy import Kairos",
        )
        offenders = []
        for root in (ROOT / "examples", ROOT / "tests"):
            for path in root.rglob("*.py"):
                if path == ROOT / "tests" / "test_repository_hygiene.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    if marker in text:
                        offenders.append(f"{path.relative_to(ROOT)} contains {marker}")
        self.assertEqual(offenders, [])

    def test_project_initializer_scaffold_uses_workspace_language(self):
        source = (ROOT / "kairospy" / "surface" / "project.py").read_text(encoding="utf-8")
        required = (
            "project_name = _project_name(name or _default_project_name(root))",
            "def _default_project_name(root: Path) -> str:",
            're.search(r\'(?m)^name\\s*=\\s*"(?:kairospy|kairospy)"\'',
            'dependencies = ["kairospy>=0.1.0"]',
            'Path(PROJECT_STATE_DIR) / "workspace"',
            'Path(PROJECT_STATE_DIR) / "run"',
            "kairospy workspace create alpha",
            "This is a Kairos quantitative data, strategy code, and run project.",
            'lake_root = "{DEFAULT_LAKE_ROOT}"',
            "Kairos-managed data lives under `.kairos/data/`",
            "Configure providers only in `kairos.toml`",
        )
        for marker in required:
            self.assertIn(marker, source)
        forbidden = (
            'dependencies = ["trader',
            'Path("config")',
            'Path("config/study.json")',
            'Path("config/research.json")',
            'Path("research',
            "python research/",
            "[research]",
            "from trading",
            "import trading",
            'Path("studies/starter.py")',
            "python studies/starter.py",
            "[study]",
        )
        for marker in forbidden:
            self.assertNotIn(marker, source)

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

    def test_source_only_packages_are_not_packaged(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = config["project"]["scripts"]
        setuptools_config = config["tool"]["setuptools"]
        package_find = setuptools_config["packages"]["find"]
        includes = package_find["include"]
        excludes = package_find.get("exclude", [])
        forbidden_packaging_files = ("MANIFEST.in", "setup.py", "setup.cfg")
        self.assertTrue((ROOT / "kairospy").is_dir())
        self.assertTrue((ROOT / "kairospy" / "research").exists())
        self.assertTrue((ROOT / "kairospy" / "research" / "capture").exists())
        self.assertTrue((ROOT / "kairospy" / "research" / "validation").exists())
        self.assertFalse((ROOT / "trading").exists())
        for filename in forbidden_packaging_files:
            self.assertFalse((ROOT / filename).exists())
        self.assertEqual(config["project"]["name"], "kairospy")
        self.assertIn("kairospy", scripts)
        self.assertNotIn("trader", scripts)
        self.assertIn("kairospy*", includes)
        self.assertNotIn("trading*", includes)
        self.assertNotIn("research*", includes)
        self.assertNotIn("package-data", setuptools_config)
        self.assertNotIn("include-package-data", setuptools_config)
        self.assertNotIn("data-files", config.get("tool", {}).get("setuptools", {}))
        self.assertNotIn("kairospy.research", excludes)
        self.assertNotIn("kairospy.research.*", excludes)

    def test_source_workspace_study_commands_are_removed_from_product_cli(self):
        cli = (ROOT / "kairospy" / "surface" / "cli" / "main.py").read_text(encoding="utf-8")
        forbidden = (
            'commands.add_parser("study"',
            "study_actions.add_parser",
            'commands.add_parser("strategy"',
            "strategy_actions.add_parser",
        )
        for marker in forbidden:
            self.assertNotIn(marker, cli)
        self.assertIn('workspace_actions.add_parser("create"', cli)

    def test_packaged_modules_do_not_import_workspace_research_at_module_load(self):
        offenders = []
        for root in (ROOT / "kairospy",):
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
        self.assertFalse((ROOT / "kairospy" / "backtest" / "mock.py").exists())
        cli = (ROOT / "kairospy" / "surface" / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('"mock"', cli)
        self.assertNotIn("backtest mock", cli)
        offenders = []
        for root in (ROOT / "kairospy",):
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if (
                        stripped.startswith(("from kairospy.backtest.mock", "import kairospy.backtest.mock"))
                        or "MockScenario" in stripped
                        or "make_mock_dataset" in stripped
                        or "curated.mock" in stripped
                    ):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_deleted_legacy_product_names_do_not_return(self):
        deleted_paths = (
            ROOT / "kairospy" / "backtest" / "mock.py",
            ROOT / "kairospy" / "adapters",
            ROOT / "kairospy" / "adapters" / "massive" / "day_aggs.py",
            ROOT / "kairospy" / "adapters" / "massive" / "equity_day_aggs.py",
            ROOT / "kairospy" / "adapters" / "massive" / "option_iv.py",
            ROOT / "kairospy" / "adapters" / "massive" / "readiness.py",
            ROOT / "kairospy" / "adapters" / "base.py",
            ROOT / "kairospy" / "adapters" / "binance" / "adapter.py",
            ROOT / "kairospy" / "adapters" / "ibkr" / "adapter.py",
            ROOT / "kairospy" / "adapters" / "composite.py",
            ROOT / "kairospy" / "backtest" / "service.py",
            ROOT / "kairospy" / "data" / "health.py",
            ROOT / "kairospy" / "features" / "us_equity_momentum_readiness.py",
            ROOT / "kairospy" / "data" / "models.py",
            ROOT / "kairospy" / "pricing" / "models.py",
            ROOT / "kairospy" / "pricing" / "option_pricing_models.py",
            ROOT / "kairospy" / "pricing" / "service.py",
            ROOT / "kairospy" / "reference" / "models.py",
            ROOT / "kairospy" / "research" / "validation" / "models.py",
            ROOT / "kairospy" / "research" / "service.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "ibkr" / "research.py",
            ROOT / "kairospy" / "portfolio" / "treasury" / "service.py",
            ROOT / "kairospy" / "portfolio" / "treasury" / "models.py",
            ROOT / "kairospy" / "portfolio" / "treasury" / "transfer_models.py",
            ROOT / "kairospy" / "volatility" / "models.py",
            ROOT / "kairospy" / "strategies" / "base.py",
            ROOT / "kairospy" / "portfolio" / "treasury" / "adapter.py",
            ROOT / "kairospy" / "application" / "runtime_failure_matrix.py",
            ROOT / "kairospy" / "application" / "runtime_golden.py",
            ROOT / "kairospy" / "application" / "task_supervisor.py",
            ROOT / "kairospy" / "backtest" / "golden.py",
            ROOT / "kairospy" / "data" / "market_slice_curation.py",
            ROOT / "kairospy" / "data" / "market_slice_storage.py",
            ROOT / "kairospy" / "research" / "analyzer.py",
            ROOT / "kairospy" / "research" / "selector.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "ibkr" / "study.py",
            ROOT / "tests" / "test_ibkr_study.py",
            ROOT / "tests" / "test_research_data_client.py",
            ROOT / "tests" / "test_option_research_capture.py",
            ROOT / "tests" / "test_crypto_option_research.py",
            ROOT / "tests" / "test_options_research_cli.py",
            ROOT / "tests" / "test_options_research_end_to_end.py",
            ROOT / "tests" / "test_research_data_store.py",
            ROOT / "tests" / "test_research_reference_evidence.py",
            ROOT / "tests" / "test_research_validation_framework.py",
            ROOT / "tests" / "test_spxw_research_analysis.py",
            ROOT / "kairospy" / "strategies" / "sma_cross_research_backtest.py",
            ROOT / "examples" / "massive_research_diagnostics.ipynb",
            ROOT / "config" / "research.json",
            ROOT / "kairospy" / "strategies" / "sma_cross.py",
            ROOT / "kairospy" / "strategies" / "sma_strategy.py",
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
            "ResearchDataClient",
            "ResearchValidationResult",
            "ResearchSnapshot",
            "OptionResearchCaptureService",
            "FileResearchRepository",
            "OptionResearchCaptureTests",
            "IbkrSpxwResearchProvider",
            "SpxwResearchProvider",
            "research_composition",
            "sma_cross_research_backtest",
            "research_spec_hash",
            "research-spec-hash",
            "research-default",
            "RESEARCH_DEFAULT_POLICY",
            "QualityLevel.RESEARCH",
            "APPROVED_FOR_RESEARCH",
            "approved_for_research",
            "@research",
            "@latest-research",
            "latest-research",
            "research-approved",
        )
        forbidden_exact_names = re.compile(r"\b(BacktestService|ValuationService|ResearchService|TreasuryService)\b")
        offenders = []
        for root in (ROOT / "kairospy", ROOT / "tests", ROOT / "examples"):
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
        self.assertFalse((ROOT / "kairospy" / "adapters").exists())
        offenders = []
        for root in (ROOT / "kairospy", ROOT / "tests", ROOT / "examples"):
            for path in root.rglob("*.py"):
                if path == ROOT / "tests" / "test_repository_hygiene.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if stripped.startswith(("from kairospy.adapters", "import kairospy.adapters")):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_kairospy_namespace_exposes_connectors_not_adapters(self):
        kairospy_init = (ROOT / "kairospy" / "__init__.py").read_text(encoding="utf-8")
        data_init = (ROOT / "kairospy" / "data" / "__init__.py").read_text(encoding="utf-8")
        runtime_init = (ROOT / "kairospy" / "runtime" / "__init__.py").read_text(encoding="utf-8")
        runtime_application = (ROOT / "kairospy" / "runtime" / "application.py").read_text(encoding="utf-8")
        runtime_async = (ROOT / "kairospy" / "runtime" / "async_runtime.py").read_text(encoding="utf-8")
        pricing_init = (ROOT / "kairospy" / "analytics" / "pricing" / "__init__.py").read_text(encoding="utf-8")
        treasury_init = (ROOT / "kairospy" / "portfolio" / "treasury" / "__init__.py").read_text(encoding="utf-8")
        capture_init = (ROOT / "kairospy" / "research" / "capture" / "__init__.py").read_text(encoding="utf-8")
        self.assertTrue((ROOT / "kairospy" / "integrations" / "connectors" / "__init__.py").exists())
        self.assertTrue((ROOT / "kairospy" / "integrations" / "ports" / "__init__.py").exists())
        self.assertTrue((ROOT / "kairospy" / "integrations" / "contracts" / "__init__.py").exists())
        self.assertTrue((ROOT / "kairospy" / "infrastructure" / "configuration.py").exists())
        self.assertTrue((ROOT / "kairospy" / "infrastructure" / "storage" / "__init__.py").exists())
        self.assertTrue((ROOT / "kairospy" / "infrastructure" / "storage" / "source_cache.py").exists())
        self.assertTrue((ROOT / "kairospy" / "surface" / "product.py").exists())
        self.assertTrue((ROOT / "kairospy" / "surface" / "data_features.py").exists())
        self.assertTrue((ROOT / "kairospy" / "surface" / "providers.py").exists())
        self.assertTrue((ROOT / "kairospy" / "surface" / "project.py").exists())
        self.assertTrue((ROOT / "kairospy" / "surface" / "cli" / "main.py").exists())
        self.assertTrue((ROOT / "kairospy" / "surface" / "cli" / "output.py").exists())
        self.assertTrue((ROOT / "kairospy" / "surface" / "cli" / "progress.py").exists())
        self.assertFalse((ROOT / "kairospy" / "connectors").exists())
        self.assertFalse((ROOT / "kairospy" / "ports").exists())
        self.assertFalse((ROOT / "kairospy" / "contracts").exists())
        self.assertFalse((ROOT / "kairospy" / "features").exists())
        self.assertFalse((ROOT / "kairospy" / "pricing").exists())
        self.assertFalse((ROOT / "kairospy" / "volatility").exists())
        self.assertFalse((ROOT / "kairospy" / "accounting").exists())
        self.assertFalse((ROOT / "kairospy" / "treasury").exists())
        self.assertFalse((ROOT / "kairospy" / "lifecycle").exists())
        self.assertFalse((ROOT / "kairospy" / "storage").exists())
        self.assertFalse((ROOT / "kairospy" / "configuration.py").exists())
        self.assertFalse((ROOT / "kairospy" / "data" / "source_cache.py").exists())
        self.assertFalse((ROOT / "kairospy" / "data" / "surface_features.py").exists())
        self.assertFalse((ROOT / "kairospy" / "product_surface.py").exists())
        self.assertFalse((ROOT / "kairospy" / "provider_surface.py").exists())
        self.assertFalse((ROOT / "kairospy" / "project.py").exists())
        self.assertFalse((ROOT / "kairospy" / "cli_output.py").exists())
        self.assertFalse((ROOT / "kairospy" / "cli_progress.py").exists())
        self.assertFalse((ROOT / "kairospy" / "capture").exists())
        self.assertFalse((ROOT / "kairospy" / "validation").exists())
        self.assertFalse((ROOT / "kairospy" / "trading" / "__init__.py").exists())
        self.assertFalse((ROOT / "kairospy" / "application").exists())
        self.assertTrue((ROOT / "kairospy" / "research").exists())
        self.assertTrue((ROOT / "kairospy" / "research" / "capture").exists())
        self.assertTrue((ROOT / "kairospy" / "research" / "validation").exists())
        self.assertFalse((ROOT / "kairospy" / "research_platform").exists())
        self.assertFalse((ROOT / "kairospy" / "study_platform").exists())
        self.assertNotIn('"adapters"', kairospy_init)
        self.assertNotIn('"task_supervisor"', kairospy_init)
        self.assertNotIn('"runtime_golden"', kairospy_init)
        self.assertNotIn('"runtime_failure_matrix"', kairospy_init)
        self.assertNotIn('"market_slice_storage"', kairospy_init)
        self.assertNotIn('"market_slice_curation"', kairospy_init)
        self.assertNotIn('"research"', kairospy_init)
        self.assertNotIn('"study_platform"', kairospy_init)
        self.assertNotIn('"Trader"', kairospy_init)
        self.assertNotIn("TradingApplication", runtime_application)
        self.assertNotIn("AsyncTradingRuntime", runtime_async)
        self.assertIn("KairosApplication", runtime_application)
        self.assertIn("AsyncKairosRuntime", runtime_async)
        self.assertNotIn('"DatasetProduct"', data_init)
        self.assertNotIn('"DatasetProductSpec"', data_init)
        self.assertNotIn("live_paper_composition", runtime_init)
        self.assertIn("paper_trading_composition", runtime_init)
        self.assertNotIn("study_composition", runtime_init)
        self.assertNotIn('"ValuationService"', pricing_init)
        self.assertNotIn('"TreasuryService"', treasury_init)
        self.assertNotIn("Research platform", capture_init)
        self.assertNotIn('"ResearchSpec"', capture_init)
        self.assertNotIn('"service"', capture_init)
        self.assertNotIn('"analyzer"', capture_init)
        self.assertNotIn('"selector"', capture_init)
        self.assertNotIn("StudyWorkspace", capture_init)
        self.assertNotIn("open_study", capture_init)
        self.assertIn('"option_capture"', capture_init)
        self.assertIn('"option_snapshot_analysis"', capture_init)
        self.assertIn('"option_universe_selector"', capture_init)
        for path in (
            ROOT / "kairospy" / "integrations" / "connectors" / "__init__.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "__init__.py",
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
        massive_init = (ROOT / "kairospy" / "integrations" / "connectors" / "massive" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("DayAgg", massive_init)
        self.assertNotIn("DayIv", massive_init)
        self.assertNotIn("Readiness", massive_init)
        for path in (ROOT / "kairospy" / "integrations" / "connectors" / "massive").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("DayAgg", text)
            self.assertNotIn("DayIv", text)
            self.assertNotIn("Readiness", text)
        self.assertFalse((ROOT / "kairospy" / "integrations" / "connectors" / "massive" / "day_aggs.py").exists())
        self.assertFalse((ROOT / "kairospy" / "integrations" / "connectors" / "massive" / "equity_day_aggs.py").exists())
        self.assertFalse((ROOT / "kairospy" / "integrations" / "connectors" / "massive" / "option_iv.py").exists())
        self.assertFalse((ROOT / "kairospy" / "integrations" / "connectors" / "massive" / "readiness.py").exists())

    def test_new_data_contract_imports_do_not_use_models_module(self):
        offenders = []
        legacy_imports = (
            "from kairospy.data.models",
            "import kairospy.data.models",
            "from kairospy.portfolio.treasury.transfer_models",
            "import kairospy.portfolio.treasury.transfer_models",
            "from kairospy.analytics.volatility.models",
            "import kairospy.analytics.volatility.models",
            "from kairospy.research.validation.models",
            "import kairospy.research.validation.models",
            "from kairospy.reference.models",
            "import kairospy.reference.models",
            "from kairospy.analytics.pricing.models",
            "import kairospy.analytics.pricing.models",
            "from kairospy.analytics.pricing.option_pricing_models",
            "import kairospy.analytics.pricing.option_pricing_models",
            "from kairospy.portfolio.treasury.models",
            "import kairospy.portfolio.treasury.models",
        )
        roots = (ROOT / "kairospy", ROOT / "tests", ROOT / "examples")
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

    def test_new_code_does_not_import_legacy_research_platform_package(self):
        offenders = []
        roots = (ROOT / "kairospy", ROOT / "tests", ROOT / "examples")
        for root in roots:
            for path in root.rglob("*.py"):
                if path == ROOT / "tests" / "test_repository_hygiene.py":
                    continue
                text = path.read_text(encoding="utf-8")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    stripped = line.strip()
                    if stripped.startswith(("from kairospy.research_platform", "import kairospy.research_platform")):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {stripped}")
        self.assertEqual(offenders, [])

    def test_public_cli_exposes_workspace_not_study_or_strategy_groups(self):
        cli = (ROOT / "kairospy" / "surface" / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('commands.add_parser("research"', cli)
        self.assertNotIn('args.group == "research"', cli)
        self.assertNotIn('commands.add_parser("study"', cli)
        self.assertNotIn('commands.add_parser("strategy"', cli)
        self.assertIn('commands.add_parser("workspace"', cli)
        self.assertIn('run_actions.add_parser("start"', cli)

    def test_new_code_does_not_import_legacy_connector_base_or_service_modules(self):
        offenders = []
        legacy_imports = (
            "from kairospy.integrations.connectors.base",
            "import kairospy.integrations.connectors.base",
            "from kairospy.strategies.base",
            "import kairospy.strategies.base",
        )
        roots = (ROOT / "kairospy", ROOT / "tests", ROOT / "examples")
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
            ROOT / "kairospy" / "surface" / "cli" / "main.py",
            ROOT / "kairospy" / "surface/product.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "binance" / "funding_ingestion.py",
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
        for root in (ROOT / "kairospy", ROOT / "tests"):
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
            ROOT / "kairospy" / "integrations" / "connectors" / "binance" / "adapter.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "ibkr" / "adapter.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "composite.py",
            ROOT / "kairospy" / "portfolio" / "treasury" / "adapter.py",
        )
        for path in deleted_paths:
            self.assertFalse(path.exists(), str(path.relative_to(ROOT)))

        offenders = []
        forbidden_imports = (
            "from kairospy.integrations.connectors.binance.adapter",
            "import kairospy.integrations.connectors.binance.adapter",
            "from kairospy.integrations.connectors.ibkr.adapter",
            "import kairospy.integrations.connectors.ibkr.adapter",
            "from kairospy.integrations.connectors.composite",
            "import kairospy.integrations.connectors.composite",
            "from kairospy.portfolio.treasury.adapter",
            "import kairospy.portfolio.treasury.adapter",
        )
        roots = (ROOT / "kairospy", ROOT / "tests", ROOT / "examples")
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
        text = (ROOT / "kairospy" / "integrations" / "connectors" / "transfer" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("BinanceTransferGateway", text)
        self.assertIn("BankTransferGateway", text)
        self.assertNotIn("BinanceTransferAdapter", text)
        self.assertNotIn("BankTransferAdapter", text)

    def test_ports_package_public_api_uses_port_names(self):
        text = (ROOT / "kairospy" / "integrations" / "ports" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("ReferenceDataPort", text)
        self.assertIn("ExecutionPort", text)
        self.assertNotIn("Adapter", text)

    def test_connector_package_public_api_does_not_reexport_adapter_aliases(self):
        packages = (
            ROOT / "kairospy" / "integrations" / "connectors" / "binance" / "__init__.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "ibkr" / "__init__.py",
            ROOT / "kairospy" / "integrations" / "connectors" / "transfer" / "__init__.py",
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

    def test_user_examples_do_not_reintroduce_study_workspace_directory(self):
        self.assertFalse((ROOT / "examples" / "research").exists())
        self.assertFalse((ROOT / "examples" / "studies").exists())

    def test_public_cli_does_not_accept_adapter_argument(self):
        text = (ROOT / "kairospy" / "surface" / "cli" / "main.py").read_text(encoding="utf-8")
        surface = (ROOT / "kairospy" / "surface/product.py").read_text(encoding="utf-8")
        self.assertNotIn("--adapter", text)
        self.assertNotIn("args.adapter", surface)

    def test_product_docs_use_kairospy_cli_name(self):
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

    def test_user_facing_examples_and_guides_import_kairospy_namespace(self):
        roots = [
            ROOT / "README.md",
            ROOT / "docs" / "data_layout.md",
            ROOT / "docs" / "data_usage_product_design.md",
            ROOT / "docs" / "study_data_guide.md",
            ROOT / "docs" / "tutorial_first_study.md",
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
                    if stripped.startswith(("from trading", "import trading")):
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
                    if "kairospy.research_platform" in stripped or "ResearchDataClient" in stripped:
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_massive_entitlement_diagnostics_is_the_public_cli_name(self):
        cli = (ROOT / "kairospy" / "surface" / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"provider-entitlement-diagnostics"', cli)
        self.assertIn('choices=("massive",)', cli)
        self.assertNotIn('"massive-entitlement-diagnostics"', cli)
        self.assertNotIn('"massive-fetch"', cli)
        self.assertNotIn('"massive-flat-file"', cli)
        self.assertNotIn('"massive-flat-file-batch"', cli)
        self.assertNotIn('"build-massive-slices"', cli)
        self.assertNotIn('"sync-massive-reference"', cli)
        self.assertNotIn('"build-massive-equity-identity"', cli)
        self.assertNotIn('"quarantine-insecure-massive-cache"', cli)
        self.assertNotIn('"massive-readiness"', cli)
        self.assertFalse((ROOT / "kairospy" / "integrations" / "connectors" / "massive" / "readiness.py").exists())
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
        cli = (ROOT / "kairospy" / "surface" / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"us-equity-momentum-diagnostics"', cli)
        self.assertNotIn('"us-equity-momentum-readiness"', cli)
        self.assertFalse((ROOT / "kairospy" / "features" / "us_equity_momentum_readiness.py").exists())
        offenders = []
        for root in (ROOT / "kairospy", ROOT / "tests", ROOT / "examples"):
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
        cli = (ROOT / "kairospy" / "surface" / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertIn('"reference-artifact"', cli)
        self.assertIn('"failure-policy"', cli)
        self.assertIn('"soak"', cli)
        self.assertNotIn('"golden"', cli)
        self.assertNotIn('"failure-matrix"', cli)
        self.assertNotIn('add_parser("trade"', cli)
        self.assertNotIn('args.group == "trade"', cli)
        self.assertFalse((ROOT / "kairospy" / "application" / "runtime_golden.py").exists())
        self.assertFalse((ROOT / "kairospy" / "application" / "runtime_failure_matrix.py").exists())

    def test_legacy_strategy_backtest_cli_and_modules_are_removed(self):
        cli = (ROOT / "kairospy" / "surface" / "cli" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn('commands.add_parser("backtest"', cli)
        self.assertNotIn('commands.add_parser("factor"', cli)
        self.assertNotIn('commands.add_parser("tutorial"', cli)
        self.assertNotIn('"spxw-reference-scenario"', cli)
        self.assertNotIn('"golden-spxw"', cli)
        self.assertFalse((ROOT / "kairospy" / "backtest" / "spxw_reference_pipeline.py").exists())
        self.assertFalse((ROOT / "kairospy" / "backtest" / "experiment_runner.py").exists())
        self.assertFalse((ROOT / "kairospy" / "backtest" / "reference_scenarios.py").exists())
        self.assertFalse((ROOT / "kairospy" / "product_workflow.py").exists())
        self.assertFalse((ROOT / "kairospy" / "api.py").exists())
        self.assertFalse((ROOT / "kairospy" / "backtest" / "golden.py").exists())
        offenders = []
        for root in (ROOT / "kairospy", ROOT / "tests", ROOT / "examples"):
            for path in root.rglob("*.py"):
                if path in {
                    ROOT / "tests" / "test_repository_hygiene.py",
                }:
                    continue
                text = path.read_text(encoding="utf-8")
                if "build_spxw_golden_pipeline" in text or "kairospy.backtest.golden" in text:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_synthetic_scenarios_use_market_snapshot_contract_names(self):
        text = (ROOT / "kairospy" / "runtime" / "profiles" / "backtest" / "synthetic_scenarios.py").read_text(encoding="utf-8")
        self.assertIn("MarketReplayDataset", text)
        self.assertIn("MarketSnapshot", text)
        self.assertIn("InstrumentLifecycleSnapshot", text)
        self.assertNotIn("HistoricalDataset", text)
        self.assertNotIn("MarketSlice", text)

    def test_workspace_data_quality_names_do_not_reintroduce_study_q2(self):
        forbidden = (
            "QualityLevel.STUDY",
            "APPROVED_FOR_STUDY",
            "approved_for_study",
            "STUDY_DEFAULT_POLICY",
            "study-default",
            "latest-study",
            "ready_for_research",
            "ready_for_study",
            "RunMode.STUDY",
            "study_composition",
            "StudyInputSnapshot",
            "write_study_snapshot",
            "freeze_study",
        )
        offenders = []
        for root in (ROOT / "kairospy", ROOT / "examples"):
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    if marker in text:
                        offenders.append(f"{path.relative_to(ROOT)} contains {marker}")
        self.assertEqual(offenders, [])

    def test_user_docs_and_examples_do_not_expose_legacy_workspace_commands(self):
        forbidden = (
            'run_mode="study"',
            "--study-id",
            "run start --study",
            "run start --snapshot",
            "kairospy run paper",
            "kairospy study ",
            "kairospy strategy ",
            "ready_for_research",
            "ready_for_study",
            "from kairospy.strategies",
            "kairospy.study_platform",
        )
        roots = [
            ROOT / "README.md",
            ROOT / "docs" / "data_product_usage.md",
            ROOT / "docs" / "connector_data_integration_usage.md",
            ROOT / "examples",
        ]
        offenders = []
        for root in roots:
            paths = (root,) if root.is_file() else root.rglob("*")
            for path in paths:
                if not path.is_file() or path.suffix not in {".py", ".md", ".ipynb", ".sh"}:
                    continue
                text = path.read_text(encoding="utf-8")
                for marker in forbidden:
                    if marker in text:
                        offenders.append(f"{path.relative_to(ROOT)} contains {marker}")
        self.assertEqual(offenders, [])
        self.assertNotIn("ContractMetadata", text)


if __name__ == "__main__":
    unittest.main()
