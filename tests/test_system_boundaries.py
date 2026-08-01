from __future__ import annotations

import re
from pathlib import Path

import kairospy.application.support.launch as launch
import kairospy.application.support.runtime as runtime
import kairospy.application.support.system as system


ROOT = Path(__file__).resolve().parents[1]


def test_system_facade_exports_launcher_without_trading_internals() -> None:
    assert system.__all__ == []
    assert "TradingSystem" not in system.__all__
    assert "TradingRuntimeResources" not in system.__all__
    assert "TradingLaunchSpec" not in system.__all__
    assert all(not name.startswith("run_") for name in system.__all__)


def test_launch_package_does_not_reexport_lifecycle_api() -> None:
    assert launch.__all__ == []
    assert not hasattr(launch, "TradingSystem")
    assert not hasattr(launch, "TradingSystemLauncher")
    assert not hasattr(launch, "LaunchFacade")


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


def test_legacy_service_modes_package_has_been_removed() -> None:
    assert not (ROOT / "kairospy" / "application" / "service" / "modes").exists()


def test_application_root_is_split_into_usecases_and_support() -> None:
    application_root = ROOT / "kairospy" / "application"
    forbidden_roots = {
        "account",
        "execution",
        "launch",
        "market",
        "reference",
        "runtime",
        "strategy",
        "system",
    }
    offenders = []
    for path in application_root.iterdir():
        if path.name in forbidden_roots and path.is_dir() and any(child.suffix == ".py" for child in path.rglob("*.py")):
            offenders.append(path.name)
    assert offenders == []

    assert (application_root / "usecases").exists()
    assert (application_root / "support").exists()
    for name in ("account", "execution", "market", "reference", "strategy"):
        assert (application_root / "usecases" / name).exists()
    for name in ("runtime", "launch", "system"):
        assert (application_root / "support" / name).exists()


def test_usecases_do_not_depend_on_support_infrastructure_or_surface() -> None:
    usecases_root = ROOT / "kairospy" / "application" / "usecases"
    forbidden = (
        "from kairospy.application.support",
        "import kairospy.application.support",
        "from kairospy.infrastructure",
        "import kairospy.infrastructure",
        "from kairospy.surface",
        "import kairospy.surface",
    )
    offenders = []
    for path in usecases_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_cli_runtime_command_envelopes_are_system_session_owned() -> None:
    strategy_cli = ROOT / "kairospy" / "application" / "usecases" / "strategy" / "cli.py"
    session_commands = ROOT / "kairospy" / "application" / "support" / "system" / "session" / "commands.py"

    assert session_commands.exists()
    assert "cli_command_envelope" not in strategy_cli.read_text(encoding="utf-8")
    assert "cli_command_envelope" in session_commands.read_text(encoding="utf-8")


def test_system_implementation_uses_orthogonal_packages() -> None:
    legacy_implementation_files = (
        ROOT / "kairospy" / "application" / "support" / "system" / "trading" / "launcher.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "trading" / "system.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "trading" / "spec.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "trading" / "lifecycle.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch" / "artifacts.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch" / "logging.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch" / "daemon.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch" / "registry.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch" / "state.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch" / "journals" / "account.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch" / "journals" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "trading" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "accounts" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "accounts" / "registry.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "connections" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "connections" / "manager.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "host" / "live_state.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "builder.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "launch_environment.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "facade" / "launch.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "facade" / "trading.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "facade" / "launch_control.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "control" / "daemon.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "control" / "registry.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "host" / "runtime_host.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "host" / "resources.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "host" / "lifecycle.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "artifacts" / "logging.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "artifacts" / "output.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "projectors" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "projectors" / "account.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "projectors" / "catalog.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "projectors" / "launch.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "projectors" / "service.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "projectors" / "timeline.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "session" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "session" / "commands.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "session" / "dispatcher.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "attach.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "authority.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "builder.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "daemon.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "facade.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "registry.py",
    )
    assert [str(path.relative_to(ROOT)) for path in legacy_implementation_files if path.exists()] == []

    expected_packages = (
        ROOT / "kairospy" / "application" / "support" / "launch" / "launcher.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "accounts" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "accounts" / "authority.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "accounts" / "scoped.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "environment" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "environment" / "builder.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "control" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "control" / "attach.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "control" / "facade.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "control" / "daemon.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "control" / "registry.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "host" / "runtime_host.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "host" / "resources.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "host" / "lifecycle.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "artifacts" / "logging.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "artifacts" / "output.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "projectors" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "projectors" / "account.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "projectors" / "catalog.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "projectors" / "launch.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "projectors" / "service.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "projectors" / "timeline.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "session" / "__init__.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "session" / "commands.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "session" / "dispatcher.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "composition" / "accounts.py",
        ROOT / "kairospy" / "application" / "support" / "system" / "resources" / "connections.py",
    )
    assert [str(path.relative_to(ROOT)) for path in expected_packages if not path.exists()] == []
    expected_infrastructure = (
        ROOT / "kairospy" / "infrastructure" / "persistence" / "artifacts" / "__init__.py",
        ROOT / "kairospy" / "infrastructure" / "persistence" / "artifacts" / "launch_store.py",
    )
    assert [str(path.relative_to(ROOT)) for path in expected_infrastructure if not path.exists()] == []
    removed_infrastructure = (
        ROOT / "kairospy" / "infrastructure" / "data" / "__init__.py",
        ROOT / "kairospy" / "infrastructure" / "artifacts" / "__init__.py",
        ROOT / "kairospy" / "infrastructure" / "artifacts" / "store.py",
    )
    assert [str(path.relative_to(ROOT)) for path in removed_infrastructure if path.exists()] == []


def test_runtime_does_not_import_system_layer() -> None:
    runtime_root = ROOT / "kairospy" / "application" / "support" / "runtime"
    offenders = []
    for path in runtime_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "application.system" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_runtime_does_not_depend_on_execution_coordinator() -> None:
    runtime_root = ROOT / "kairospy" / "application" / "support" / "runtime"
    offenders = []
    for path in runtime_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ExecutionCoordinator" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_runtime_processors_do_not_depend_on_ports() -> None:
    processors_root = ROOT / "kairospy" / "application" / "support" / "runtime" / "processors"
    forbidden = (
        "application.ports",
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


def test_subscription_models_are_owned_by_market_area() -> None:
    assert not (ROOT / "kairospy" / "application" / "ports" / "subscriptions.py").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "market" / "subscriptions.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "application.ports.subscriptions" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_reference_contracts_are_owned_by_reference_area() -> None:
    assert not (ROOT / "kairospy" / "application" / "ports" / "reference_store.py").exists()
    assert not (ROOT / "kairospy" / "application" / "ports" / "reference_catalog.py").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "reference" / "store.py").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "reference" / "source.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    forbidden = (
        "application.ports.reference_store",
        "application.ports.reference_catalog",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_reference_use_cases_are_owned_by_reference_area() -> None:
    assert not (ROOT / "kairospy" / "application" / "domain" / "reference").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "reference" / "operations.py").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "reference" / "refresh.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "application.domain.reference" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_market_data_contracts_are_owned_by_market_area() -> None:
    assert not (ROOT / "kairospy" / "application" / "ports" / "market_history.py").exists()
    assert not (ROOT / "kairospy" / "application" / "ports" / "market_storage.py").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "market" / "history.py").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "market" / "datasets.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    forbidden = (
        "application.ports.market_history",
        "application.ports.market_storage",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_market_use_cases_are_owned_by_market_area() -> None:
    assert not (ROOT / "kairospy" / "application" / "domain" / "market").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "market" / "operations.py").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "market" / "resolver.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "application.domain.market" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_application_domain_package_has_been_removed() -> None:
    assert not (ROOT / "kairospy" / "application" / "domain").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "application.domain" in text or "kairospy.application.domain" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_application_ports_package_has_been_removed() -> None:
    assert not (ROOT / "kairospy" / "application" / "ports").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "application.ports" in text or "kairospy.application.ports" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_integration_services_are_imported_from_target_package() -> None:
    assert not (ROOT / "kairospy" / "infrastructure" / "integrations" / "credentials.py").exists()
    assert not (ROOT / "kairospy" / "infrastructure" / "integrations" / "registry.py").exists()
    assert not (ROOT / "kairospy" / "infrastructure" / "integrations" / "resolver.py").exists()
    assert (ROOT / "kairospy" / "infrastructure" / "integrations" / "services" / "resolver.py").exists()
    assert (ROOT / "kairospy" / "infrastructure" / "integrations" / "services" / "registry.py").exists()
    assert (ROOT / "kairospy" / "infrastructure" / "integrations" / "services" / "credentials.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    forbidden = (
        "kairospy.infrastructure.integrations.credentials",
        "kairospy.infrastructure.integrations.registry",
        "kairospy.infrastructure.integrations.resolver",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_integration_root_does_not_keep_compatibility_modules() -> None:
    integration_root = ROOT / "kairospy" / "infrastructure" / "integrations"
    forbidden_files = (
        "equities.py",
        "instruments.py",
        "model.py",
        "simulated.py",
    )
    assert [name for name in forbidden_files if (integration_root / name).exists()] == []

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    forbidden_imports = tuple(f"kairospy.infrastructure.integrations.{name.removesuffix('.py')}" for name in forbidden_files)
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden_imports:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_integration_registry_uses_participants_without_adapter_alias() -> None:
    assert "IntegrationAdapter" not in (
        ROOT / "kairospy" / "infrastructure" / "integrations" / "protocols.py"
    ).read_text(encoding="utf-8")
    assert "IntegrationAdapter" not in (
        ROOT / "kairospy" / "infrastructure" / "integrations" / "services" / "registry.py"
    ).read_text(encoding="utf-8")


def test_integration_domain_does_not_depend_on_outer_layers() -> None:
    domain_root = ROOT / "kairospy" / "infrastructure" / "integrations" / "domain"
    assert domain_root.exists()
    assert (domain_root / "participants.py").exists()
    assert (domain_root / "capabilities.py").exists()
    assert (domain_root / "bindings.py").exists()
    assert (domain_root / "policies.py").exists()
    forbidden = (
        "kairospy.application",
        "kairospy.surface",
        "kairospy.infrastructure.persistence",
        "kairospy.infrastructure.integrations.connectors",
        "kairospy.infrastructure.integrations.drivers",
        "kairospy.infrastructure.integrations.payloads",
        "kairospy.infrastructure.integrations.adapters",
    )
    offenders = []
    for path in domain_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_application_account_routing_does_not_encode_integration_sdk_params() -> None:
    text = (ROOT / "kairospy" / "application" / "usecases" / "account" / "routing.py").read_text(encoding="utf-8")
    forbidden = (
        "defaultType",
        "marginMode",
        "isIsolated",
        "portfolio_margin",
        "_ccxt",
        "kairospy.infrastructure",
    )

    assert [marker for marker in forbidden if marker in text] == []


def test_integration_adapters_are_owned_by_adapters_package() -> None:
    integration_root = ROOT / "kairospy" / "infrastructure" / "integrations"
    assert not (integration_root / "market_stream.py").exists()
    assert not (integration_root / "reference_catalog.py").exists()
    assert not (integration_root / "order_execution.py").exists()
    assert not (integration_root / "equities.py").exists()
    assert not (integration_root / "instruments.py").exists()
    assert (integration_root / "adapters" / "market_stream.py").exists()
    assert (integration_root / "adapters" / "reference_catalog.py").exists()
    assert (integration_root / "adapters" / "order_execution.py").exists()
    assert (integration_root / "adapters" / "reference_snapshot.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    forbidden = (
        "kairospy.infrastructure.integrations.market_stream",
        "kairospy.infrastructure.integrations.reference_catalog",
        "kairospy.infrastructure.integrations.order_execution",
        "kairospy.infrastructure.integrations.equities",
        "kairospy.infrastructure.integrations.instruments",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_integration_connectors_do_not_write_persistence() -> None:
    connector_root = ROOT / "kairospy" / "infrastructure" / "integrations" / "connectors"
    forbidden = (
        "infrastructure.persistence.market_data.ingest",
        "DataSink",
        "persist_ticker",
        "persist_order_book",
        "persist_trades",
    )
    offenders = []
    for path in connector_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_integration_connectors_do_not_depend_on_application_layers() -> None:
    checked_roots = (
        ROOT / "kairospy" / "infrastructure" / "integrations" / "connectors",
        ROOT / "kairospy" / "infrastructure" / "integrations" / "drivers",
        ROOT / "kairospy" / "infrastructure" / "integrations" / "payloads",
    )
    forbidden = (
        "kairospy.application",
        "from kairospy.application",
    )
    offenders = []
    for root in checked_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_integration_services_do_not_depend_on_application_layers() -> None:
    services_root = ROOT / "kairospy" / "infrastructure" / "integrations" / "services"
    offenders = []
    for path in services_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "kairospy.application" in text or "from kairospy.application" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_integrations_do_not_depend_on_persistence_records() -> None:
    integration_root = ROOT / "kairospy" / "infrastructure" / "integrations"
    forbidden = (
        "from kairospy.infrastructure.persistence",
        "import kairospy.infrastructure.persistence",
    )
    offenders = []
    for path in integration_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_integration_types_are_split_by_role() -> None:
    integration_root = ROOT / "kairospy" / "infrastructure" / "integrations"
    assert not (integration_root / "types.py").exists()
    assert (integration_root / "payloads" / "types.py").exists()
    assert not (integration_root / "streams.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "kairospy.infrastructure.integrations.types" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_application_ports_are_imported_from_concrete_modules() -> None:
    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "from kairospy.application.ports import" in text or "import kairospy.application.ports" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_application_support_models_are_owned_by_runtime_or_system() -> None:
    removed_top_level = (
        ROOT / "kairospy" / "application" / "protocol",
        ROOT / "kairospy" / "application" / "query",
        ROOT / "kairospy" / "application" / "views",
        ROOT / "kairospy" / "application" / "browsing",
        ROOT / "kairospy" / "application" / "pagination.py",
        ROOT / "kairospy" / "application" / "modes.py",
        ROOT / "kairospy" / "application" / "account_books.py",
    )
    offenders = []
    for path in removed_top_level:
        if path.is_file():
            offenders.append(str(path.relative_to(ROOT)))
        elif path.is_dir() and any(child.suffix == ".py" for child in path.rglob("*.py")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []

    assert (ROOT / "kairospy" / "application" / "support" / "runtime" / "events.py").exists()
    assert (ROOT / "kairospy" / "application" / "support" / "runtime" / "lines.py").exists()
    assert (ROOT / "kairospy" / "application" / "support" / "runtime" / "query").exists()
    assert (ROOT / "kairospy" / "application" / "support" / "runtime" / "views").exists()
    assert (ROOT / "kairospy" / "application" / "support" / "launch" / "modes.py").exists()
    assert (ROOT / "kairospy" / "application" / "support" / "system" / "browsing").exists()
    assert (ROOT / "kairospy" / "application" / "support" / "system" / "pagination.py").exists()
    assert (ROOT / "kairospy" / "application" / "usecases" / "account" / "books.py").exists()


def test_application_support_models_are_imported_from_owning_modules() -> None:
    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            forbidden = (
                "kairospy.application.protocol",
                "kairospy.application.query",
                "kairospy.application.views",
                "kairospy.application.browsing",
                "kairospy.application.pagination",
                "kairospy.application.modes",
                "kairospy.application.account_books",
            )
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_application_strategy_is_imported_from_concrete_modules() -> None:
    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "from kairospy.application.usecases.strategy import" in text or "from kairospy.application import" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_runtime_execution_uses_execution_runtime_contract() -> None:
    from kairospy.application.support.runtime.components import ExecutionRuntime
    from kairospy.application.support.runtime.services.application import RuntimeExecutionService, TradingRuntimeExecutionService

    assert "submit_intent" in ExecutionRuntime.__dict__
    assert "port" in TradingRuntimeExecutionService.__dataclass_fields__
    assert "projection" in TradingRuntimeExecutionService.__dataclass_fields__
    assert "updates" in TradingRuntimeExecutionService.__dataclass_fields__
    assert "intent_executor" not in TradingRuntimeExecutionService.__dataclass_fields__
    assert "trading" in RuntimeExecutionService.__dataclass_fields__
    assert "projection" not in RuntimeExecutionService.__dataclass_fields__
    assert "updates" not in RuntimeExecutionService.__dataclass_fields__


def test_shared_read_paths_do_not_import_runtime_processors() -> None:
    checked_roots = (
        ROOT / "kairospy" / "application" / "service",
        ROOT / "kairospy" / "application" / "usecases" / "strategy",
        ROOT / "kairospy" / "application" / "support" / "system" / "resources",
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
    processors_root = ROOT / "kairospy" / "application" / "support" / "runtime" / "processors"
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
    services_root = ROOT / "kairospy" / "application" / "support" / "runtime" / "services"
    forbidden = ("ExecutionCurrentProjection", "ExecutionFillsProjection")
    offenders = []
    for path in services_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_runtime_service_public_api_does_not_reexport_execution_view_contracts() -> None:
    import kairospy.application.support.runtime.services as runtime_service

    forbidden = {"ExecutionCurrentView", "ExecutionFillSummary", "ExecutionFillsView", "ExecutionOrderSummary"}
    assert forbidden.isdisjoint(set(runtime_service.__all__))


def test_system_artifacts_do_not_import_mode_recipes() -> None:
    artifacts_root = ROOT / "kairospy" / "application" / "support" / "system" / "artifacts"
    offenders = []
    for path in artifacts_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "application.service.modes" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_mode_config_recipes_do_not_construct_runtime_account_resources() -> None:
    config_files = (
        ROOT / "kairospy" / "application" / "support" / "launch" / "config" / "backtest.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "config" / "paper.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "config" / "live.py",
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


def test_account_runtime_resources_are_owned_by_launch_composition() -> None:
    assert not (ROOT / "kairospy" / "application" / "support" / "system" / "resources" / "accounts.py").exists()
    assert (ROOT / "kairospy" / "application" / "support" / "launch" / "composition" / "accounts.py").exists()

    roots = (
        ROOT / "kairospy",
        ROOT / "tests",
        ROOT / "examples",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            if path == Path(__file__):
                continue
            text = path.read_text(encoding="utf-8")
            if "application.system.resources.accounts" in text:
                offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_launch_config_does_not_choose_infrastructure_implementations() -> None:
    config_root = ROOT / "kairospy" / "application" / "support" / "launch" / "config"
    forbidden = (
        "kairospy.infrastructure.integrations",
        "kairospy.infrastructure.persistence",
        "kairospy.application.support.launch.composition",
    )
    offenders = []
    for path in config_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_launch_infrastructure_selection_is_owned_by_composition() -> None:
    launch_root = ROOT / "kairospy" / "application" / "support" / "launch"
    allowed = launch_root / "composition"
    forbidden = (
        "kairospy.infrastructure.integrations",
        "kairospy.infrastructure.persistence",
    )
    offenders = []
    for path in launch_root.rglob("*.py"):
        if path.is_relative_to(allowed):
            continue
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_system_facade_does_not_choose_infrastructure_implementations() -> None:
    facade_root = ROOT / "kairospy" / "application" / "support" / "system" / "facade"
    forbidden = (
        "kairospy.infrastructure.integrations",
        "kairospy.infrastructure.persistence",
    )
    offenders = []
    for path in facade_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_system_layer_does_not_choose_infrastructure_implementations() -> None:
    system_root = ROOT / "kairospy" / "application" / "support" / "system"
    offenders = []
    for path in system_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "kairospy.infrastructure" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_account_workspace_records_use_broker_as_primary_identity() -> None:
    from kairospy.application.support.system.workspace.accounts import AccountRecord
    from kairospy.application.support.system.workspace.credentials import CredentialRecord

    assert "broker" in AccountRecord.__dataclass_fields__
    assert "provider" not in AccountRecord.__dataclass_fields__
    assert "broker" in CredentialRecord.__dataclass_fields__
    assert "provider" not in CredentialRecord.__dataclass_fields__


def test_surface_does_not_import_launch_composition() -> None:
    checked_roots = (
        ROOT / "kairospy" / "surface",
        ROOT / "examples",
    )
    offenders = []
    for root in checked_roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "kairospy.application.support.launch.composition" in text:
                offenders.append(str(path.relative_to(ROOT)))
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


def test_system_and_surface_do_not_import_runtime_internals() -> None:
    roots = (
        ROOT / "kairospy" / "application" / "support" / "system",
        ROOT / "kairospy" / "surface",
    )
    forbidden = (
        "kairospy.application.support.runtime.dispatch",
        "kairospy.application.support.runtime.orchestration",
        "kairospy.application.support.runtime.processors",
        "kairospy.application.support.runtime.services",
    )
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_surface_does_not_import_lower_layers_directly() -> None:
    surface_root = ROOT / "kairospy" / "surface"
    forbidden = (
        "kairospy.infrastructure",
        "kairospy.application.support.runtime",
        "kairospy.application.support.runtime.services",
        "kairospy.application.domain",
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
        ROOT / "kairospy" / "application" / "support" / "launch" / "control" / "daemon.py",
        ROOT / "kairospy" / "application" / "support" / "launch" / "control" / "registry.py",
    )
    for path in control_files:
        text = path.read_text(encoding="utf-8")
        assert '"instances"' in text
        assert '/ "launches" / instance_id' not in text
        assert '/ "launches" / str(identity["process_id"])' not in text
