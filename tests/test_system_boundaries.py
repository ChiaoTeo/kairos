from __future__ import annotations

from pathlib import Path

import kairospy.application.system as system


ROOT = Path(__file__).resolve().parents[1]


def test_system_facade_exports_launcher_without_trading_internals() -> None:
    assert system.__all__ == ["RunAlreadyActiveError", "RunControl", "TradingConfigurationError", "TradingSystemLauncher"]
    assert "TradingSystem" not in system.__all__
    assert "TradingRuntimeResources" not in system.__all__
    assert "TradingRunSpec" not in system.__all__
    assert all(not name.startswith("run_") for name in system.__all__)


def test_service_modes_do_not_import_system_trading_startup() -> None:
    service_modes = ROOT / "kairospy" / "application" / "service" / "modes"
    forbidden = (
        "application.system.trading",
        "TradingSystem",
        "TradingRuntimeResources",
        "TradingRunSpec",
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
        ROOT / "kairospy" / "application" / "system" / "run" / "artifacts.py",
        ROOT / "kairospy" / "application" / "system" / "run" / "logging.py",
        ROOT / "kairospy" / "application" / "system" / "run" / "daemon.py",
        ROOT / "kairospy" / "application" / "system" / "run" / "registry.py",
        ROOT / "kairospy" / "application" / "system" / "run" / "state.py",
        ROOT / "kairospy" / "application" / "system" / "run" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "run" / "journals" / "account.py",
        ROOT / "kairospy" / "application" / "system" / "run" / "journals" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "trading" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "accounts" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "accounts" / "registry.py",
        ROOT / "kairospy" / "application" / "system" / "connections" / "__init__.py",
        ROOT / "kairospy" / "application" / "system" / "connections" / "manager.py",
        ROOT / "kairospy" / "application" / "system" / "host" / "live_state.py",
    )
    assert [str(path.relative_to(ROOT)) for path in legacy_implementation_files if path.exists()] == []

    expected_packages = (
        ROOT / "kairospy" / "application" / "system" / "facade" / "trading.py",
        ROOT / "kairospy" / "application" / "system" / "host" / "runtime_host.py",
        ROOT / "kairospy" / "application" / "system" / "host" / "resources.py",
        ROOT / "kairospy" / "application" / "system" / "control" / "daemon.py",
        ROOT / "kairospy" / "application" / "system" / "control" / "registry.py",
        ROOT / "kairospy" / "application" / "system" / "artifacts" / "writer.py",
        ROOT / "kairospy" / "application" / "system" / "artifacts" / "logging.py",
        ROOT / "kairospy" / "application" / "system" / "artifacts" / "journals" / "account.py",
        ROOT / "kairospy" / "application" / "system" / "resources" / "accounts.py",
        ROOT / "kairospy" / "application" / "system" / "resources" / "connections.py",
        ROOT / "kairospy" / "application" / "system" / "resources" / "live_state.py",
    )
    assert [str(path.relative_to(ROOT)) for path in expected_packages if not path.exists()] == []


def test_runtime_does_not_import_system_layer() -> None:
    runtime_root = ROOT / "kairospy" / "application" / "runtime"
    offenders = []
    for path in runtime_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "application.system" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


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


def test_surface_uses_system_facade_for_run_startup() -> None:
    surface_files = (
        ROOT / "kairospy" / "surface" / "cli" / "commands" / "run.py",
    )
    forbidden = (
        "RuntimeKernel",
        "RuntimeRunSpec",
        "TradingSystem(",
        "TradingRuntimeResources",
        "TradingRunSpec",
        "application.system.trading",
    )
    offenders = []
    for path in surface_files:
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_surface_run_startup_does_not_import_mode_recipes() -> None:
    surface_files = (
        ROOT / "kairospy" / "surface" / "cli" / "commands" / "run.py",
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


def test_run_control_uses_instances_directory_for_run_instances() -> None:
    control_files = (
        ROOT / "kairospy" / "application" / "system" / "control" / "daemon.py",
        ROOT / "kairospy" / "application" / "system" / "control" / "registry.py",
    )
    for path in control_files:
        text = path.read_text(encoding="utf-8")
        assert '"instances"' in text
        assert '/ "runs" / instance_id' not in text
        assert '/ "runs" / str(identity["process_id"])' not in text
