from __future__ import annotations

from pathlib import Path
import re
import stat
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

    def test_local_build_artifacts_are_not_present(self):
        forbidden = []
        for path in (ROOT / "build", ROOT / "dist"):
            if path.exists():
                forbidden.append(str(path.relative_to(ROOT)))
        forbidden.extend(str(path.relative_to(ROOT)) for path in ROOT.glob("*.egg-info"))
        self.assertEqual(forbidden, [])

    def test_notebook_checkpoints_are_not_present(self):
        files = [path for path in ROOT.rglob("*") if path.is_file() and ".ipynb_checkpoints" in path.parts]
        self.assertEqual(files, [])

    def test_workspace_file_names_do_not_reintroduce_legacy_or_generic_terms(self):
        ignored_parts = {".git", "pyenv", ".venv", ".pytest_cache"}
        forbidden_exact = {
            "adapters",
            "base.py",
            "handler.py",
            "helpers.py",
            "manager.py",
            "models.py",
            "research",
            "research.py",
            "service.py",
            "trader",
            "trading",
            "utils.py",
        }
        forbidden_fragments = ("adapter", "research")
        offenders = []
        stack = [ROOT]
        while stack:
            current = stack.pop()
            for path in current.iterdir():
                relative = path.relative_to(ROOT)
                if path.is_dir() and path.name in ignored_parts:
                    continue
                name = path.name
                if name in forbidden_exact or any(fragment in name for fragment in forbidden_fragments):
                    offenders.append(str(relative))
                if path.is_dir() and not path.is_symlink():
                    stack.append(path)
        self.assertEqual(offenders, [])

    def test_static_naming_acceptance_script_is_documented_and_executable(self):
        script = ROOT / "scripts" / "check_naming_static.sh"
        script_text = script.read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        audit = (ROOT / "docs" / "naming_audit.md").read_text(encoding="utf-8")
        self.assertTrue(script.exists())
        self.assertTrue(script.stat().st_mode & stat.S_IXUSR)
        self.assertIn("git diff --check", script_text)
        self.assertIn("MANIFEST.in", script_text)
        self.assertIn("package-data", script_text)
        self.assertIn("./scripts/check_naming_static.sh", readme)
        self.assertIn("./scripts/check_naming_static.sh", audit)

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
            "python studies/starter.py",
            "安装包只包含 Kairos 产品库和 CLI，不包含本仓库顶层 `studies/` 源码研究工作区",
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

    def test_root_kairospy_toml_uses_study_section(self):
        config = (ROOT / "kairos.toml").read_text(encoding="utf-8")
        self.assertIn('name = "kairospy"', config)
        self.assertIn("[study]", config)
        self.assertNotIn("[research]", config)
        self.assertNotIn('name = "trader"', config)

    def test_package_public_namespace_does_not_export_legacy_project_names(self):
        packages = (
            ROOT / "kairospy" / "__init__.py",
            ROOT / "kairospy" / "connectors" / "__init__.py",
            ROOT / "kairospy" / "ports" / "__init__.py",
            ROOT / "kairospy" / "study_platform" / "__init__.py",
        )
        offenders = []
        forbidden = ('"adapters"', '"research"', '"trader"', '"trading"', "Adapter", "ResearchSpec", "ResearchService")
        for path in packages:
            text = path.read_text(encoding="utf-8")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path.relative_to(ROOT)} contains {token}")
        self.assertEqual(offenders, [])

    def test_project_initializer_scaffold_uses_kairospy_study_workspace_language(self):
        source = (ROOT / "kairospy" / "project.py").read_text(encoding="utf-8")
        required = (
            "project_name = _project_name(name or _default_project_name(root))",
            "def _default_project_name(root: Path) -> str:",
            're.search(r\'(?m)^name\\s*=\\s*"(?:kairospy|kairospy)"\'',
            'dependencies = ["kairospy>=0.1.0"]',
            'Path("studies/starter.py")',
            "python studies/starter.py",
            "[study]",
            "This is a Kairos quantitative study, backtest, and execution project.",
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

    def test_studies_workspace_is_not_packaged(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = config["project"]["scripts"]
        setuptools_config = config["tool"]["setuptools"]
        package_find = setuptools_config["packages"]["find"]
        includes = package_find["include"]
        excludes = package_find["exclude"]
        forbidden_packaging_files = ("MANIFEST.in", "setup.py", "setup.cfg")
        self.assertTrue((ROOT / "kairospy").is_dir())
        self.assertFalse((ROOT / "kairospy" / "research").exists())
        self.assertFalse((ROOT / "trading").exists())
        for filename in forbidden_packaging_files:
            self.assertFalse((ROOT / filename).exists())
        self.assertEqual(config["project"]["name"], "kairospy")
        self.assertIn("kairospy", scripts)
        self.assertNotIn("trader", scripts)
        self.assertIn("kairospy*", includes)
        self.assertNotIn("trading*", includes)
        self.assertNotIn("research*", includes)
        self.assertNotIn("studies*", includes)
        self.assertNotIn("package-data", setuptools_config)
        self.assertNotIn("include-package-data", setuptools_config)
        self.assertNotIn("data-files", config.get("tool", {}).get("setuptools", {}))
        self.assertIn("kairospy.research", excludes)
        self.assertIn("kairospy.research.*", excludes)
        self.assertIn("research", excludes)
        self.assertIn("research.*", excludes)
        self.assertIn("studies", excludes)
        self.assertIn("studies.*", excludes)

    def test_source_workspace_study_commands_are_hidden_from_product_help(self):
        cli = (ROOT / "kairospy" / "__main__.py").read_text(encoding="utf-8")
        required = (
            'data_actions.add_parser("btc-options-readiness", help=argparse.SUPPRESS)',
            'study_actions.add_parser("register-btc-iron-condor", help=argparse.SUPPRESS)',
            'study_actions.add_parser("readiness", help=argparse.SUPPRESS)',
        )
        for marker in required:
            self.assertIn(marker, cli)
        self.assertIn("is not included in the pip package", cli)

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
        cli = (ROOT / "kairospy" / "__main__.py").read_text(encoding="utf-8")
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
            ROOT / "kairospy" / "connectors" / "ibkr" / "research.py",
            ROOT / "kairospy" / "treasury" / "service.py",
            ROOT / "kairospy" / "treasury" / "models.py",
            ROOT / "kairospy" / "treasury" / "transfer_models.py",
            ROOT / "kairospy" / "volatility" / "models.py",
            ROOT / "kairospy" / "strategies" / "base.py",
            ROOT / "kairospy" / "treasury" / "adapter.py",
            ROOT / "kairospy" / "application" / "runtime_failure_matrix.py",
            ROOT / "kairospy" / "application" / "runtime_golden.py",
            ROOT / "kairospy" / "application" / "task_supervisor.py",
            ROOT / "kairospy" / "backtest" / "golden.py",
            ROOT / "kairospy" / "data" / "market_slice_curation.py",
            ROOT / "kairospy" / "data" / "market_slice_storage.py",
            ROOT / "kairospy" / "research" / "analyzer.py",
            ROOT / "kairospy" / "research" / "selector.py",
            ROOT / "kairospy" / "connectors" / "ibkr" / "study.py",
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
            "RESEARCH_VALIDATED",
            "research-default",
            "RESEARCH_DEFAULT_POLICY",
            "QualityLevel.RESEARCH",
            "APPROVED_FOR_RESEARCH",
            "approved_for_research",
            "@research",
            "@latest-research",
            "latest-research",
            "research_quality",
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
        domain_init = (ROOT / "kairospy" / "domain" / "__init__.py").read_text(encoding="utf-8")
        application_init = (ROOT / "kairospy" / "application" / "__init__.py").read_text(encoding="utf-8")
        pricing_init = (ROOT / "kairospy" / "pricing" / "__init__.py").read_text(encoding="utf-8")
        treasury_init = (ROOT / "kairospy" / "treasury" / "__init__.py").read_text(encoding="utf-8")
        study_platform_init = (ROOT / "kairospy" / "study_platform" / "__init__.py").read_text(encoding="utf-8")
        self.assertTrue((ROOT / "kairospy" / "connectors" / "__init__.py").exists())
        self.assertFalse((ROOT / "kairospy" / "research").exists())
        self.assertFalse((ROOT / "kairospy" / "research_platform").exists())
        self.assertNotIn('"adapters"', kairospy_init)
        self.assertNotIn('"task_supervisor"', kairospy_init)
        self.assertNotIn('"runtime_golden"', kairospy_init)
        self.assertNotIn('"runtime_failure_matrix"', kairospy_init)
        self.assertNotIn('"market_slice_storage"', kairospy_init)
        self.assertNotIn('"market_slice_curation"', kairospy_init)
        self.assertNotIn('"research"', kairospy_init)
        self.assertIn('"study_platform"', kairospy_init)
        self.assertNotIn('"Trader"', kairospy_init)
        self.assertNotIn("TradingApplication", application_init)
        self.assertNotIn("AsyncTradingRuntime", application_init)
        self.assertIn("KairosApplication", application_init)
        self.assertIn("AsyncKairosRuntime", application_init)
        self.assertNotIn('"DatasetProduct"', data_init)
        self.assertNotIn('"DatasetProductSpec"', data_init)
        self.assertNotIn('"ProductSpec"', domain_init)
        self.assertNotIn("live_paper_composition", application_init)
        self.assertIn("paper_trading_composition", application_init)
        self.assertIn("study_composition", application_init)
        self.assertNotIn('"ValuationService"', pricing_init)
        self.assertNotIn('"TreasuryService"', treasury_init)
        self.assertNotIn("Research platform", study_platform_init)
        self.assertNotIn('"ResearchSpec"', study_platform_init)
        self.assertNotIn('"service"', study_platform_init)
        self.assertNotIn('"analyzer"', study_platform_init)
        self.assertNotIn('"selector"', study_platform_init)
        self.assertIn('"option_capture"', study_platform_init)
        self.assertIn('"option_snapshot_analysis"', study_platform_init)
        self.assertIn('"option_universe_selector"', study_platform_init)
        for path in (
            ROOT / "kairospy" / "connectors" / "__init__.py",
            ROOT / "kairospy" / "connectors" / "__init__.py",
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
        massive_init = (ROOT / "kairospy" / "connectors" / "massive" / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("DayAgg", massive_init)
        self.assertNotIn("DayIv", massive_init)
        self.assertNotIn("Readiness", massive_init)
        for path in (ROOT / "kairospy" / "connectors" / "massive").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("DayAgg", text)
            self.assertNotIn("DayIv", text)
            self.assertNotIn("Readiness", text)
        self.assertFalse((ROOT / "kairospy" / "connectors" / "massive" / "day_aggs.py").exists())
        self.assertFalse((ROOT / "kairospy" / "connectors" / "massive" / "equity_day_aggs.py").exists())
        self.assertFalse((ROOT / "kairospy" / "connectors" / "massive" / "option_iv.py").exists())
        self.assertFalse((ROOT / "kairospy" / "connectors" / "massive" / "readiness.py").exists())

    def test_new_data_contract_imports_do_not_use_models_module(self):
        offenders = []
        legacy_imports = (
            "from kairospy.data.models",
            "import kairospy.data.models",
            "from kairospy.treasury.transfer_models",
            "import kairospy.treasury.transfer_models",
            "from kairospy.volatility.models",
            "import kairospy.volatility.models",
            "from kairospy.study_platform.validation.models",
            "import kairospy.study_platform.validation.models",
            "from kairospy.reference.models",
            "import kairospy.reference.models",
            "from kairospy.pricing.models",
            "import kairospy.pricing.models",
            "from kairospy.pricing.option_pricing_models",
            "import kairospy.pricing.option_pricing_models",
            "from kairospy.treasury.models",
            "import kairospy.treasury.models",
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

    def test_user_data_guides_use_dataset_client_as_public_name(self):
        offenders = []
        for path in (
            ROOT / "README.md",
            ROOT / "docs" / "data_layout.md",
            ROOT / "docs" / "study_data_guide.md",
            ROOT / "docs" / "data_usage_product_design.md",
        ):
            text = path.read_text(encoding="utf-8")
            if "ResearchDataClient" in text:
                offenders.append(str(path.relative_to(ROOT)))
        data_init = (ROOT / "kairospy" / "data" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn('"DatasetClient"', data_init)
        self.assertEqual(offenders, [])

    def test_run_product_guides_use_study_mode_as_public_name(self):
        offenders = []
        for path in (
            ROOT / "docs" / "data_usage_product_design.md",
            ROOT / "README.md",
        ):
            text = path.read_text(encoding="utf-8")
            if "--mode research" in text:
                offenders.append(str(path.relative_to(ROOT)))
        application_modes = (ROOT / "kairospy" / "application" / "modes.py").read_text(encoding="utf-8")
        data_contracts = (ROOT / "kairospy" / "data" / "contracts.py").read_text(encoding="utf-8")
        self.assertIn("def study_composition", application_modes)
        self.assertIn('STUDY = "study"', data_contracts)
        self.assertEqual(offenders, [])

    def test_public_cli_does_not_expose_research_command_group(self):
        cli = (ROOT / "kairospy" / "__main__.py").read_text(encoding="utf-8")
        self.assertNotIn('commands.add_parser("research"', cli)
        self.assertNotIn('args.group == "research"', cli)
        self.assertIn('commands.add_parser("study"', cli)
        self.assertIn('study_actions.add_parser("capture"', cli)
        self.assertIn('study_actions.add_parser("capture-series"', cli)

    def test_strategy_guides_use_study_validated_as_public_lifecycle_name(self):
        offenders = []
        for path in (
            ROOT / "README.md",
            ROOT / "docs" / "data_usage_product_design.md",
        ):
            text = path.read_text(encoding="utf-8")
            for marker in ("RESEARCH_VALIDATED", "research-default"):
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)} contains {marker}")
        strategy_contract = (ROOT / "kairospy" / "domain" / "strategy_contract.py").read_text(encoding="utf-8")
        data_init = (ROOT / "kairospy" / "data" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("STUDY_VALIDATED", strategy_contract)
        self.assertIn('"STUDY_DEFAULT_POLICY"', data_init)
        self.assertEqual(offenders, [])

    def test_new_code_does_not_import_legacy_connector_base_or_service_modules(self):
        offenders = []
        legacy_imports = (
            "from kairospy.connectors.base",
            "import kairospy.connectors.base",
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
            ROOT / "kairospy" / "__main__.py",
            ROOT / "kairospy" / "product_surface.py",
            ROOT / "kairospy" / "connectors" / "binance" / "funding_ingestion.py",
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
            ROOT / "kairospy" / "connectors" / "binance" / "adapter.py",
            ROOT / "kairospy" / "connectors" / "ibkr" / "adapter.py",
            ROOT / "kairospy" / "connectors" / "composite.py",
            ROOT / "kairospy" / "treasury" / "adapter.py",
        )
        for path in deleted_paths:
            self.assertFalse(path.exists(), str(path.relative_to(ROOT)))

        offenders = []
        forbidden_imports = (
            "from kairospy.connectors.binance.adapter",
            "import kairospy.connectors.binance.adapter",
            "from kairospy.connectors.ibkr.adapter",
            "import kairospy.connectors.ibkr.adapter",
            "from kairospy.connectors.composite",
            "import kairospy.connectors.composite",
            "from kairospy.treasury.adapter",
            "import kairospy.treasury.adapter",
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
        text = (ROOT / "kairospy" / "connectors" / "transfer" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("BinanceTransferGateway", text)
        self.assertIn("BankTransferGateway", text)
        self.assertNotIn("BinanceTransferAdapter", text)
        self.assertNotIn("BankTransferAdapter", text)

    def test_ports_package_public_api_uses_port_names(self):
        text = (ROOT / "kairospy" / "ports" / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("ReferenceDataPort", text)
        self.assertIn("ExecutionPort", text)
        self.assertNotIn("Adapter", text)

    def test_connector_package_public_api_does_not_reexport_adapter_aliases(self):
        packages = (
            ROOT / "kairospy" / "connectors" / "binance" / "__init__.py",
            ROOT / "kairospy" / "connectors" / "ibkr" / "__init__.py",
            ROOT / "kairospy" / "connectors" / "transfer" / "__init__.py",
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

    def test_user_study_examples_use_studies_directory(self):
        self.assertFalse((ROOT / "examples" / "research").exists())
        self.assertTrue((ROOT / "examples" / "studies").exists())

    def test_user_facing_docs_use_connector_language(self):
        docs = (
            ROOT / "README.md",
            ROOT / "examples" / "README.md",
            ROOT / "examples" / "connectors" / "reference_connector" / "README.md",
            ROOT / "docs" / "architecture.md",
            ROOT / "docs" / "system_convergence_progress.md",
            ROOT / "docs" / "system_architecture_convergence_blueprint.md",
            ROOT / "docs" / "study_strategy_backtest_live_convergence_plan.md",
        )
        offenders = []
        for path in docs:
            text = path.read_text(encoding="utf-8")
            if "Adapter" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_public_cli_does_not_accept_adapter_argument(self):
        text = (ROOT / "kairospy" / "__main__.py").read_text(encoding="utf-8")
        surface = (ROOT / "kairospy" / "product_surface.py").read_text(encoding="utf-8")
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
                    if stripped.startswith(("from trading", "import trading")) or "trading." in stripped:
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
                    if "kairospy.research_platform" in stripped or "ResearchDataClient" in stripped:
                        offenders.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
        self.assertEqual(offenders, [])

    def test_massive_entitlement_diagnostics_is_the_public_cli_name(self):
        cli = (ROOT / "kairospy" / "__main__.py").read_text(encoding="utf-8")
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
        self.assertFalse((ROOT / "kairospy" / "connectors" / "massive" / "readiness.py").exists())
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
        cli = (ROOT / "kairospy" / "__main__.py").read_text(encoding="utf-8")
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
        cli = (ROOT / "kairospy" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn('"reference-artifact"', cli)
        self.assertIn('"failure-policy"', cli)
        self.assertIn('"soak"', cli)
        self.assertNotIn('"golden"', cli)
        self.assertNotIn('"failure-matrix"', cli)
        self.assertNotIn('add_parser("trade"', cli)
        self.assertNotIn('args.group == "trade"', cli)
        self.assertFalse((ROOT / "kairospy" / "application" / "runtime_golden.py").exists())
        self.assertFalse((ROOT / "kairospy" / "application" / "runtime_failure_matrix.py").exists())

    def test_spxw_reference_scenario_is_the_public_backtest_cli_name(self):
        cli = (ROOT / "kairospy" / "__main__.py").read_text(encoding="utf-8")
        self.assertIn('"spxw-reference-scenario"', cli)
        self.assertNotIn('"golden-spxw"', cli)
        self.assertTrue((ROOT / "kairospy" / "backtest" / "spxw_reference_pipeline.py").exists())
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
        text = (ROOT / "kairospy" / "backtest" / "synthetic_scenarios.py").read_text(encoding="utf-8")
        self.assertIn("MarketReplayDataset", text)
        self.assertIn("MarketSnapshot", text)
        self.assertIn("InstrumentLifecycleSnapshot", text)
        self.assertNotIn("HistoricalDataset", text)
        self.assertNotIn("MarketSlice", text)
        self.assertNotIn("ContractMetadata", text)


if __name__ == "__main__":
    unittest.main()
