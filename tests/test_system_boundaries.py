from __future__ import annotations

import re
from pathlib import Path

import kairospy.application.launch as launch
import kairospy.application.runtime as runtime
import kairospy.application.system as system


ROOT = Path(__file__).resolve().parents[1]


def test_system_facade_exports_launcher_without_trading_internals() -> None:
    assert system.__all__ == []
    assert "TradingSystem" not in system.__all__
    assert "TradingRuntimeResources" not in system.__all__
    assert "TradingLaunchSpec" not in system.__all__
    assert all(not name.startswith("run_") for name in system.__all__)


def test_launch_package_exports_launch_lifecycle_api() -> None:
    assert launch.__all__ == [
        "LaunchAccountBinding",
        "LaunchAccountDirectory",
        "LaunchAlreadyActiveError",
        "LaunchBuilder",
        "LaunchControl",
        "LaunchEnvironment",
        "LaunchFacade",
        "TradingConfigurationError",
        "TradingSystemLauncher",
    ]
    assert "TradingSystem" not in launch.__all__
    assert all(not name.startswith("run_") for name in launch.__all__)


def test_runtime_package_does_not_export_service_container() -> None:
    assert "RuntimeApplicationServices" not in runtime.__all__
    assert not hasattr(runtime, "RuntimeApplicationServices")


def test_launch_language_does_not_reintroduce_run_control_names() -> None:
    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
        ROOT / "docs",
        ROOT / "README.md",
    )
    forbidden = (
        "run_control",
        "--run-id",
        "run_id",
        "RuntimePortPipeline",
        "launch cli",
        "launch_cli_commands",
        "open_cli_session",
        'command("cli")',
        "kairos-cli-launch",
    )
    offenders = []
    for root in roots:
        paths = (root,) if root.is_file() else root.rglob("*")
        for path in paths:
            if path.is_dir() or path.suffix not in {".py", ".md", ".toml", ".jsonl"}:
                continue
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_service_modes_do_not_import_system_trading_startup() -> None:
    service_modes = ROOT / "kairospy" / "application" / "service" / "modes"
    forbidden = (
        "application.system.trading",
        "TradingSystem",
        "TradingRuntimeResources",
        "TradingLaunchSpec",
    )
    offenders = []
    for path in service_modes.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_system_implementation_uses_orthogonal_packages() -> None:
    legacy_implementation_files = (
        ROOT / "kairospy" / "application" / "system" / "trading" / "launcher.py",
        ROOT / "kairospy" / "application" / "system" / "trading" / "system.py",
        ROOT / "kairospy" / "application" / "system" / "trading" / "spec.py",
        ROOT / "kairospy" / "application" / "system" / "trading" / "lifecycle.py",
        ROOT / "kairospy" / "application" / "system" / "launch" / "artifacts.py",
        ROOT / "kairospy" / "application" / "system" / "launch" / "logging.py",
        ROOT / "kairospy" / "application" / "system" / "launch" / "daemon.py",
        ROOT / "kairospy" / "application" / "system" / "launch" / "registry.py",
        ROOT / "kairospy" / "application" / "system" / "launch" / "state.py",
        ROOT / "kairospy" / "application" / "system" / "launch" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "launch" / "journals" / "account.py",
        ROOT / "kairospy" / "application" / "system" / "launch" / "journals" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "trading" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "accounts" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "accounts" / "registry.py",
        ROOT / "kairospy" / "application" / "system" / "connections" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "connections" / "manager.py",
        ROOT / "kairospy" / "application" / "system" / "host" / "live_state.py",
        ROOT / "kairospy" / "application" / "system" / "builder.py",
        ROOT / "kairospy" / "application" / "system" / "launch_environment.py",
        ROOT / "kairospy" / "application" / "system" / "facade" / "launch.py",
        ROOT / "kairospy" / "application" / "system" / "facade" / "trading.py",
        ROOT / "kairospy" / "application" / "system" / "facade" / "launch_control.py",
        ROOT / "kairospy" / "application" / "system" / "control" / "daemon.py",
        ROOT / "kairospy" / "application" / "system" / "control" / "registry.py",
        ROOT / "kairospy" / "application" / "system" / "host" / "runtime_host.py",
        ROOT / "kairospy" / "application" / "system" / "host" / "resources.py",
        ROOT / "kairospy" / "application" / "system" / "host" / "lifecycle.py",
        ROOT / "kairospy" / "application" / "launch" / "artifacts" / "logging.py",
        ROOT / "kairospy" / "application" / "launch" / "artifacts" / "output.py",
        ROOT / "kairospy" / "application" / "launch" / "projectors" / "__init__.py",
        ROOT / "kairospy" / "application" / "launch" / "projectors" / "account.py",
        ROOT / "kairospy" / "application" / "launch" / "projectors" / "catalog.py",
        ROOT / "kairospy" / "application" / "launch" / "projectors" / "launch.py",
        ROOT / "kairospy" / "application" / "launch" / "projectors" / "service.py",
        ROOT / "kairospy" / "application" / "launch" / "projectors" / "timeline.py",
        ROOT / "kairospy" / "application" / "launch" / "session" / "__init__.py",
        ROOT / "kairospy" / "application" / "launch" / "session" / "commands.py",
        ROOT / "kairospy" / "application" / "launch" / "session" / "dispatcher.py",
    )
    assert [str(path.relative_to(ROOT)) for path in legacy_implementation_files if path.exists()] == []

    expected_packages = (
        ROOT / "kairospy" / "application" / "launch" / "facade.py",
        ROOT / "kairospy" / "application" / "launch" / "launcher.py",
        ROOT / "kairospy" / "application" / "launch" / "builder.py",
        ROOT / "kairospy" / "application" / "launch" / "environment.py",
        ROOT / "kairospy" / "application" / "launch" / "control.py",
        ROOT / "kairospy" / "application" / "launch" / "daemon.py",
        ROOT / "kairospy" / "application" / "launch" / "registry.py",
        ROOT / "kairospy" / "application" / "launch" / "host" / "runtime_host.py",
        ROOT / "kairospy" / "application" / "launch" / "host" / "resources.py",
        ROOT / "kairospy" / "application" / "launch" / "host" / "lifecycle.py",
        ROOT / "kairospy" / "application" / "system" / "artifacts" / "logging.py",
        ROOT / "kairospy" / "application" / "system" / "artifacts" / "output.py",
        ROOT / "kairospy" / "application" / "system" / "projectors" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "projectors" / "account.py",
        ROOT / "kairospy" / "application" / "system" / "projectors" / "catalog.py",
        ROOT / "kairospy" / "application" / "system" / "projectors" / "launch.py",
        ROOT / "kairospy" / "application" / "system" / "projectors" / "service.py",
        ROOT / "kairospy" / "application" / "system" / "projectors" / "timeline.py",
        ROOT / "kairospy" / "application" / "system" / "session" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "session" / "commands.py",
        ROOT / "kairospy" / "application" / "system" / "session" / "dispatcher.py",
        ROOT / "kairospy" / "application" / "system" / "resources" / "accounts.py",
        ROOT / "kairospy" / "application" / "system" / "resources" / "connections.py",
        ROOT / "kairospy" / "application" / "system" / "resources" / "live_state.py",
    )
    assert [str(path.relative_to(ROOT)) for path in expected_packages if not path.exists()] == []
    expected_infrastructure = (
        ROOT / "kairospy" / "infrastructure" / "artifacts" / "__init__.py",
        ROOT / "kairospy" / "infrastructure" / "artifacts" / "store.py",
    )
    assert [str(path.relative_to(ROOT)) for path in expected_infrastructure if not path.exists()] == []


def test_runtime_does_not_import_system_layer() -> None:
    runtime_root = ROOT / "kairospy" / "application" / "runtime"
    offenders = []
    for path in runtime_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "application.system" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_runtime_does_not_depend_on_execution_coordinator() -> None:
    runtime_root = ROOT / "kairospy" / "application" / "runtime"
    offenders = []
    for path in runtime_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ExecutionCoordinator" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_runtime_processors_do_not_depend_on_ports() -> None:
    processors_root = ROOT / "kairospy" / "application" / "runtime" / "processors"
    forbidden = (
        "application.ports",
        "AccountPort",
        "MarketDataPort",
        "ReferencePort",
        "TradingExecutionPort",
    )
    offenders = []
    for path in processors_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if re.search(rf"\b{re.escape(marker)}\b", text):
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_shared_read_paths_do_not_import_runtime_processors() -> None:
    checked_roots = (
        ROOT / "kairospy" / "application" / "service",
        ROOT / "kairospy" / "application" / "strategy",
        ROOT / "kairospy" / "application" / "system" / "resources",
        ROOT / "kairospy" / "surface" / "cli",
    )
    offenders = []
    for root in checked_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "application.runtime.processors" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_runtime_processors_do_not_define_shared_view_contracts() -> None:
    processors_root = ROOT / "kairospy" / "application" / "runtime" / "processors"
    forbidden_classes = (
        "StrategyLaunchView",
        "SystemEventView",
        "RiskEventView",
        "EquityCurveView",
        "OrderCurrentView",
        "IntentJournalView",
        "DecisionTraceView",
        "RiskSnapshotsView",
        "ExecutionCurrentView",
        "ExecutionFillsView",
    )
    offenders = []
    for path in processors_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in forbidden_classes:
            if re.search(rf"^class\s+{name}\b", text, flags=re.MULTILINE):
                offenders.append(f"{path.relative_to(ROOT)}:{name}")
    assert offenders == []


def test_runtime_services_do_not_export_legacy_execution_projection_aliases() -> None:
    services_root = ROOT / "kairospy" / "application" / "service" / "runtime"
    forbidden = ("ExecutionCurrentProjection", "ExecutionFillsProjection")
    offenders = []
    for path in services_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_runtime_service_public_api_does_not_reexport_execution_view_contracts() -> None:
    import kairospy.application.service.runtime as runtime_service

    forbidden = {"ExecutionCurrentView", "ExecutionFillSummary", "ExecutionFillsView", "ExecutionOrderSummary"}
    assert forbidden.isdisjoint(set(runtime_service.__all__))


def test_system_artifacts_do_not_import_mode_recipes() -> None:
    artifacts_root = ROOT / "kairospy" / "application" / "system" / "artifacts"
    offenders = []
    for path in artifacts_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "application.service.modes" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_service_modes_do_not_import_system_layer() -> None:
    service_modes = ROOT / "kairospy" / "application" / "service" / "modes"
    offenders = []
    for path in service_modes.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if "application.system." not in line:
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{line_number}:{line.strip()}")
    assert offenders == []


def test_service_modes_do_not_construct_live_state_resources() -> None:
    service_modes = ROOT / "kairospy" / "application" / "service" / "modes"
    offenders = []
    for path in service_modes.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "application.system.resources.live_state" in text or "JsonLiveRuntimeStateStore" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_mode_config_recipes_do_not_construct_runtime_account_resources() -> None:
    config_files = (
        ROOT / "kairospy" / "application" / "service" / "modes" / "backtest" / "config.py",
        ROOT / "kairospy" / "application" / "service" / "modes" / "paper" / "config.py",
        ROOT / "kairospy" / "application" / "service" / "modes" / "live" / "config.py",
    )
    forbidden = (
        "BacktestAccountService",
        "BacktestExecutionService",
        "PaperAccountService",
        "PaperExecutionService",
        "LiveAccountService",
        "LiveExecutionService",
        "ExecutionCoordinator",
        "SimulatedAccount",
    )
    offenders = []
    for path in config_files:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_surface_uses_system_facade_for_launch_startup() -> None:
    surface_files = (
        ROOT / "kairospy" / "surface" / "cli" / "commands" / "launch.py",
    )
    forbidden = (
        "RuntimeKernel",
        "RuntimeLaunchSpec",
        "TradingSystem(",
        "TradingRuntimeResources",
        "TradingLaunchSpec",
        "application.system.trading",
    )
    offenders = []
    for path in surface_files:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_surface_launch_startup_does_not_import_mode_recipes() -> None:
    surface_files = (
        ROOT / "kairospy" / "surface" / "cli" / "commands" / "launch.py",
    )
    offenders = []
    for path in surface_files:
        text = path.read_text(encoding="utf-8")
        if "application.service.modes" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_surface_does_not_import_system_control_internals() -> None:
    surface_root = ROOT / "kairospy" / "surface"
    offenders = []
    for path in surface_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "application.system.control" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_surface_does_not_import_lower_layers_directly() -> None:
    surface_root = ROOT / "kairospy" / "surface"
    forbidden = (
        "kairospy.infrastructure",
        "kairospy.application.runtime",
        "kairospy.application.service.modes",
        "kairospy.application.service.runtime",
        "kairospy.application.service.domain",
        "kairospy.core",
    )
    offenders = []
    for path in surface_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(marker in line for marker in forbidden):
                offenders.append(f"{path.relative_to(ROOT)}:{line_number}:{line.strip()}")
    assert offenders == []


def test_surface_runtime_bridge_has_been_removed() -> None:
    assert not (ROOT / "kairospy" / "surface" / "runtime.py").exists()


def test_surface_uses_cli_interactive_rendering_packages() -> None:
    surface_root = ROOT / "kairospy" / "surface"
    removed_paths = (
        surface_root / "app.py",
        surface_root / "cli.py",
        surface_root / "cli" / "command_index.py",
        surface_root / "command_tree.py",
        surface_root / "render_text.py",
        surface_root / "state.py",
        surface_root / "tui.py",
        surface_root / "products",
        surface_root / "ui",
    )
    assert [str(path.relative_to(ROOT)) for path in removed_paths if path.exists()] == []
    assert (surface_root / "cli" / "app.py").exists()
    assert (surface_root / "cli" / "commands").is_dir()
    assert (surface_root / "interactive" / "navigation.py").exists()
    assert (surface_root / "interactive" / "shell.py").exists()
    assert (surface_root / "interactive" / "session.py").exists()
    assert (surface_root / "rendering" / "text.py").exists()


def test_surface_interactive_does_not_use_clirunner() -> None:
    interactive_root = ROOT / "kairospy" / "surface" / "interactive"
    offenders = []
    for path in interactive_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "CliRunner" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_surface_has_no_second_command_group_abstraction() -> None:
    surface_root = ROOT / "kairospy" / "surface"
    forbidden = ("CommandTree", "CommandNode", "CommandGroup", "TyperCommandIndex")
    offenders = []
    for path in surface_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_surface_commands_use_shared_output_format() -> None:
    commands_root = ROOT / "kairospy" / "surface" / "cli" / "commands"
    offenders = []
    for path in commands_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "class OutputFormat" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_surface_does_not_use_workspace_internals_directly() -> None:
    surface_root = ROOT / "kairospy" / "surface"
    forbidden = (
        "application.system.workspace",
        "kairospy.config",
        "KairosWorkspace",
        "OperationJournal",
        "AccountRecord",
    )
    offenders = []
    for path in surface_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(marker in line for marker in forbidden):
                offenders.append(f"{path.relative_to(ROOT)}:{line_number}:{line.strip()}")
    assert offenders == []


def test_launch_control_uses_instances_directory_for_launch_instances() -> None:
    control_files = (
        ROOT / "kairospy" / "application" / "launch" / "daemon.py",
        ROOT / "kairospy" / "application" / "launch" / "registry.py",
    )
    for path in control_files:
        text = path.read_text(encoding="utf-8")
        assert '"instances"' in text
        assert '/ "launches" / instance_id' not in text
        assert '/ "launches" / str(identity["process_id"])' not in text
