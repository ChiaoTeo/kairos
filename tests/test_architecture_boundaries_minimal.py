from __future__ import annotations

from pathlib import Path

from kairospy.core.execution import ExecutionCoordinator
from kairospy.application.service.domains.execution import LiveExecutionAdapter, SimulatedExecutionAdapter
from kairospy.application.service.domains.market.records import ticker_record
from kairospy.application.context import ControlRequestKind, StrategyContext
from kairospy.application.runtime.model import RuntimeDataEnvelope
from kairospy.application.strategy import StrategySignal


ROOT = Path(__file__).resolve().parents[1]


def test_deprecated_trading_package_is_removed() -> None:
    assert not (ROOT / "kairospy" / "trading").exists()


def test_top_level_package_layout_matches_current_architecture() -> None:
    allowed = {
        "application",
        "core",
        "infrastructure",
        "surface",
    }
    actual = {
        path.name
        for path in (ROOT / "kairospy").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert actual == allowed


def test_old_top_level_domain_and_mode_packages_are_removed() -> None:
    removed = {
        "accounts",
        "backtest",
        "execution",
        "intents",
        "live",
        "market",
        "orders",
        "paper",
        "reference",
    }
    existing = sorted(name for name in removed if (ROOT / "kairospy" / name).exists())
    assert existing == []


def test_architecture_docs_cover_current_migration_boundary() -> None:
    architecture = ROOT / "docs" / "architecture.md"
    runtime_layers = ROOT / "docs" / "runtime_layers.md"
    audit = ROOT / "docs" / "migration_audit.md"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert architecture.exists()
    assert runtime_layers.exists()
    assert audit.exists()
    assert "docs/architecture.md" in readme
    assert "docs/runtime_layers.md" in readme
    assert "docs/migration_audit.md" in readme
    assert "runtime_layers.md" in architecture.read_text(encoding="utf-8")


def test_execution_domain_owns_execution_names() -> None:
    assert ExecutionCoordinator.__module__ == "kairospy.core.execution.coordinator"
    assert LiveExecutionAdapter.__module__ == "kairospy.application.service.domains.execution.live"
    assert SimulatedExecutionAdapter.__module__ == "kairospy.application.service.domains.execution.simulation"


def test_architecture_dependency_direction_is_enforced() -> None:
    forbidden_by_root = {
        ROOT / "kairospy" / "core": (
            "kairospy.application.strategy",
            "kairospy.application.runtime",
            "kairospy.application.mode",
            "kairospy.infrastructure.integrations",
            "kairospy.surface",
        ),
        ROOT / "kairospy" / "application" / "strategy": (
            "kairospy.application.runtime",
            "kairospy.application.mode",
            "kairospy.infrastructure.integrations",
            "kairospy.surface",
        ),
        ROOT / "kairospy" / "application" / "runtime": (
            "kairospy.infrastructure.integrations",
            "kairospy.application.mode",
            "kairospy.surface",
        ),
        ROOT / "kairospy" / "infrastructure" / "integrations": (
            "kairospy.application.runtime",
            "kairospy.application.mode",
            "kairospy.surface",
        ),
    }
    offenders = []
    for root, forbidden in forbidden_by_root.items():
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for item in forbidden:
                if item in text:
                    offenders.append(f"{path.relative_to(ROOT).as_posix()} imports {item}")
    assert offenders == []


def test_runtime_layer_does_not_grow_external_assembly_dependencies() -> None:
    forbidden = (
        "kairospy.infrastructure.data",
        "kairospy.application.mode",
        "kairospy.infrastructure.integrations",
        "kairospy.surface",
    )
    allowed_service_imports = {
        "kairospy/application/runtime/kernel/context.py",
        "kairospy/application/runtime/kernel/kernel.py",
        "kairospy/application/runtime/kernel/output.py",
        "kairospy/application/runtime/kernel/state.py",
        "kairospy/application/runtime/projection/market/store.py",
    }
    allowed_config_imports: set[str] = set()
    offenders = []
    for path in (ROOT / "kairospy" / "application" / "runtime").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(ROOT).as_posix()
        text = path.read_text(encoding="utf-8")
        for item in forbidden:
            if item in text:
                offenders.append(f"{relative} imports {item}")
        if "kairospy.application.service" in text and relative not in allowed_service_imports:
            offenders.append(f"{relative} imports kairospy.application.service")
        if "kairospy.config" in text and relative not in allowed_config_imports:
            offenders.append(f"{relative} imports kairospy.config")
    assert offenders == []


def test_runtime_kernel_uses_runtime_facing_services_not_ports_layer() -> None:
    runtime_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "kairospy" / "application" / "runtime").rglob("*.py")
        if "__pycache__" not in path.parts
    )
    services_text = (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "services.py").read_text(encoding="utf-8")

    assert "class RuntimeServices" in services_text
    assert "RuntimePorts" not in runtime_text
    assert "RuntimeCapabilities" not in runtime_text


def test_runtime_does_not_export_market_row_sources() -> None:
    runtime_exports = __import__("kairospy.application.runtime", fromlist=["__all__"]).__all__
    market_exports = __import__("kairospy.application.service.domains.market", fromlist=["__all__"]).__all__
    source_names = {
        "AsyncDataViewEventSource",
        "AsyncIterableEventSource",
        "DataViewEventSource",
        "IterableEventSource",
        "runtime_envelope_from_row",
    }

    assert source_names.isdisjoint(runtime_exports)
    assert source_names.issubset(market_exports)


def test_runtime_does_not_export_account_bootstrap_helpers() -> None:
    runtime_exports = __import__("kairospy.application.runtime", fromlist=["__all__"]).__all__
    account_exports = __import__("kairospy.application.service.domains.account", fromlist=["__all__"]).__all__

    assert "account_baseline_event" not in runtime_exports
    assert "account_baseline_event" in account_exports


def test_runtime_uses_runtime_runner_naming() -> None:
    runtime_exports = __import__("kairospy.application.runtime", fromlist=["__all__"]).__all__

    assert "RuntimeRunner" in runtime_exports
    assert "RuntimeRunResult" in runtime_exports
    assert "RuntimeRunSession" in runtime_exports
    assert "ModeRunner" not in runtime_exports
    assert "ModeRunResult" not in runtime_exports
    assert "ModeRunSession" not in runtime_exports


def test_runtime_run_spec_does_not_assemble_account_projection() -> None:
    from kairospy.application.runtime.run import RuntimeRunResult, RuntimeRunSpec

    runner_text = (ROOT / "kairospy" / "application" / "runtime" / "run" / "runner.py").read_text(encoding="utf-8")
    spec_text = (ROOT / "kairospy" / "application" / "runtime" / "run" / "spec.py").read_text(encoding="utf-8")

    assert "account" not in RuntimeRunSpec.__dataclass_fields__
    assert "equity_currency" not in RuntimeRunSpec.__dataclass_fields__
    assert "initial_equity" not in RuntimeRunSpec.__dataclass_fields__
    assert "data" not in RuntimeRunSpec.__dataclass_fields__
    assert "market_resolver" not in RuntimeRunSpec.__dataclass_fields__
    assert "components" not in RuntimeRunSpec.__dataclass_fields__
    assert "intent_handler" not in RuntimeRunSpec.__dataclass_fields__
    assert "subscription_handler" not in RuntimeRunSpec.__dataclass_fields__
    assert "request_providers" not in RuntimeRunSpec.__dataclass_fields__
    assert {"state_config", "service_config", "projection_config"}.issubset(RuntimeRunSpec.__dataclass_fields__)
    assert set(RuntimeRunResult.__dataclass_fields__) == {"runtime", "views"}
    assert "AccountCurrentProjection" not in runner_text
    assert "AccountCurrentView" not in runner_text
    assert "class RuntimeRunSpec" in spec_text


def test_runtime_spine_has_explicit_step_output_queue_and_session_modules() -> None:
    model_exports = __import__("kairospy.application.runtime.model", fromlist=["__all__"]).__all__
    kernel_exports = __import__("kairospy.application.runtime.kernel", fromlist=["__all__"]).__all__
    run_exports = __import__("kairospy.application.runtime.run", fromlist=["__all__"]).__all__
    projection_exports = __import__("kairospy.application.runtime.projection", fromlist=["__all__"]).__all__
    runner_text = (ROOT / "kairospy" / "application" / "runtime" / "run" / "runner.py").read_text(encoding="utf-8")
    kernel_text = (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "kernel.py").read_text(encoding="utf-8")

    assert "RuntimeStep" in model_exports
    assert "RuntimeStepResult" in model_exports
    assert "RuntimeOutputProcessor" in kernel_exports
    assert "RuntimeEngine" in kernel_exports
    assert "RuntimeStepProcessor" not in kernel_exports
    assert not (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "step.py").exists()
    assert "RuntimeQueue" in kernel_exports
    assert "RuntimeRunSpec" in run_exports
    assert "RuntimeRunSession" in run_exports
    assert "RuntimeAsyncEnvelopeBridge" in run_exports
    assert "RuntimeEnvelopePump" not in run_exports
    assert "RuntimeProjectionGroup" not in projection_exports
    assert "def run(spec: RuntimeRunSpec)" in runner_text
    assert "async def run_async" not in runner_text
    assert "async def run_async" not in kernel_text
    assert "run_spec" not in runner_text


def test_runtime_kernel_delegates_system_view_projection() -> None:
    loop_text = (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "kernel.py").read_text(encoding="utf-8")
    system_text = (
        ROOT / "kairospy" / "application" / "runtime" / "projection" / "system" / "runtime.py"
    ).read_text(encoding="utf-8")
    intent_text = (
        ROOT / "kairospy" / "application" / "runtime" / "projection" / "intent" / "journal.py"
    ).read_text(encoding="utf-8")

    assert "RuntimeSystemProjection" in loop_text
    assert "IntentJournalProjection" in loop_text
    assert "ControlJournalView" not in loop_text
    assert "IntentJournalView" not in loop_text
    assert "StrategyRunView" not in loop_text
    assert "ControlJournalView" in system_text
    assert "IntentJournalView" not in system_text
    assert "IntentJournalView" in intent_text
    assert "StrategyRunView" in system_text


def test_runtime_kernel_delegates_market_view_projection() -> None:
    loop_text = (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "kernel.py").read_text(encoding="utf-8")
    registry_text = (ROOT / "kairospy" / "application" / "runtime" / "projection" / "registry.py").read_text(encoding="utf-8")
    projector_text = (ROOT / "kairospy" / "application" / "runtime" / "projection" / "market" / "projector.py").read_text(encoding="utf-8")
    market_text = (ROOT / "kairospy" / "application" / "runtime" / "projection" / "market" / "__init__.py").read_text(encoding="utf-8")
    market_publisher_text = (
        ROOT / "kairospy" / "application" / "runtime" / "projection" / "market" / "publisher.py"
    ).read_text(encoding="utf-8")

    assert "RuntimeProjectionRegistry" in loop_text
    assert "MarketProjection" in loop_text
    assert "market_state.publish_views" not in loop_text
    assert "self.market_state.apply_envelope" not in loop_text
    assert "self.market.apply_envelope" not in registry_text
    assert "self.market.apply_envelope" in projector_text
    assert 'put_runtime("market.' not in loop_text
    assert 'put_runtime("market.' in market_publisher_text
    assert 'put_runtime("market.' not in market_text
    assert "class MarketStore" not in market_text


def test_market_projection_is_split_into_focused_modules() -> None:
    projection_root = ROOT / "kairospy" / "application" / "runtime" / "projection"
    market_text = (projection_root / "market" / "__init__.py").read_text(encoding="utf-8")
    store_text = (projection_root / "market" / "store.py").read_text(encoding="utf-8")
    projector_text = (projection_root / "market" / "projector.py").read_text(encoding="utf-8")
    registry_text = (projection_root / "registry.py").read_text(encoding="utf-8")
    publisher_text = (projection_root / "market" / "publisher.py").read_text(encoding="utf-8")
    access_text = (projection_root / "market" / "access.py").read_text(encoding="utf-8")
    views_text = (projection_root / "market" / "views.py").read_text(encoding="utf-8")

    assert "class MarketStore" in store_text
    assert "class MarketProjection" in projector_text
    assert "class MarketProjection" not in registry_text
    assert "class MarketViewPublisher" in publisher_text
    assert "class MarketAccess" in access_text
    assert "class MarketQuotesView" in views_text
    assert "class MarketStore" not in market_text
    assert "class MarketViewPublisher" not in market_text
    assert "from .publisher import MarketViewPublisher" in market_text

    for relative in (
        "kairospy/application/runtime/kernel/context.py",
        "kairospy/application/runtime/kernel/state.py",
        "kairospy/application/runtime/kernel/kernel.py",
        "kairospy/application/runtime/projection/registry.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "projection.market_" not in text
        assert "from .market_" not in text


def test_projection_domains_are_packaged_consistently() -> None:
    projection_root = ROOT / "kairospy" / "application" / "runtime" / "projection"
    for domain in ("account", "execution", "intent", "market", "order", "reference", "risk", "system"):
        assert (projection_root / domain / "__init__.py").exists()

    assert (projection_root / "base.py").exists()
    assert (projection_root / "registry.py").exists()
    assert not (projection_root / "components.py").exists()
    assert not (projection_root / "group.py").exists()
    assert not (projection_root / "market.py").exists()

    projection_exports = set(__import__("kairospy.application.runtime.projection", fromlist=["__all__"]).__all__)
    assert {
        "AccountCurrentProjection",
        "ExecutionCurrentProjection",
        "IntentJournalProjection",
        "RiskEventProjection",
        "RuntimeSystemProjection",
        "MarketProjection",
    }.issubset(projection_exports)


def test_core_domains_do_not_own_runtime_view_components() -> None:
    for relative in (
        "kairospy/core/account/views.py",
        "kairospy/core/market/views.py",
        "kairospy/core/execution/views.py",
    ):
        assert not (ROOT / relative).exists()

    core_roots = (
        ROOT / "kairospy" / "core" / "account",
        ROOT / "kairospy" / "core" / "market",
        ROOT / "kairospy" / "core" / "execution",
        ROOT / "kairospy" / "core" / "intent",
        ROOT / "kairospy" / "core" / "order",
        ROOT / "kairospy" / "core" / "reference",
    )
    offenders = []
    for root in core_roots:
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "ViewSchema" in text or "ViewStore" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_core_views_only_exports_shared_primitives() -> None:
    view_exports = __import__("kairospy.core.views", fromlist=["__all__"]).__all__
    runtime_system_exports = __import__("kairospy.application.runtime.projection.system", fromlist=["__all__"]).__all__
    runtime_intent_exports = __import__("kairospy.application.runtime.projection.intent", fromlist=["__all__"]).__all__

    assert not (ROOT / "kairospy" / "core" / "views.py").exists()
    assert (ROOT / "kairospy" / "core" / "views" / "__init__.py").exists()
    assert (ROOT / "kairospy" / "core" / "views" / "schema.py").exists()
    assert (ROOT / "kairospy" / "core" / "views" / "store.py").exists()
    assert (ROOT / "kairospy" / "core" / "views" / "registry.py").exists()
    assert (ROOT / "kairospy" / "core" / "views" / "envelope.py").exists()
    assert (ROOT / "kairospy" / "core" / "views" / "hashing.py").exists()
    assert (ROOT / "kairospy" / "core" / "views" / "defaults.py").exists()
    assert "StrategyRunView" not in view_exports
    assert "ControlJournalView" not in view_exports
    assert "ControlRequestSummary" not in view_exports
    assert "IntentJournalView" not in view_exports
    assert "IntentStateSummary" not in view_exports
    assert "StrategyRunView" in runtime_system_exports
    assert "ControlJournalView" in runtime_system_exports
    assert "IntentJournalView" in runtime_intent_exports


def test_runtime_projection_owns_runtime_current_views() -> None:
    projection_root = ROOT / "kairospy" / "application" / "runtime" / "projection"
    assert "class AccountCurrentProjection" in (projection_root / "account" / "current.py").read_text(encoding="utf-8")
    assert "class MarketCurrentProjection" in (projection_root / "market" / "current.py").read_text(encoding="utf-8")
    assert "class ExecutionCurrentProjection" in (projection_root / "execution" / "current.py").read_text(encoding="utf-8")


def test_market_projection_does_not_own_external_request_provider() -> None:
    projection_exports = __import__("kairospy.application.runtime.projection", fromlist=["__all__"]).__all__
    kernel_exports = __import__("kairospy.application.runtime.kernel", fromlist=["__all__"]).__all__
    market_root = ROOT / "kairospy" / "application" / "runtime" / "projection" / "market"
    market_text = "\n".join(path.read_text(encoding="utf-8") for path in market_root.glob("*.py"))

    assert "MarketDataRequestProvider" not in projection_exports
    assert "RuntimeRequestProviders" not in projection_exports
    assert "MarketRequestService" not in projection_exports
    assert "quote_from_mapping" not in projection_exports
    assert "class MarketDataRequestProvider" not in market_text
    assert "class RuntimeRequestProviders" not in market_text
    assert "class MarketRequestService" not in market_text
    assert "MarketDataRequestProvider" in kernel_exports
    assert "RuntimeRequestProviders" in kernel_exports
    assert "MarketRequestService" in kernel_exports


def test_runtime_kernel_delegates_context_creation() -> None:
    kernel_exports = __import__("kairospy.application.runtime.kernel", fromlist=["__all__"]).__all__
    loop_text = (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "kernel.py").read_text(encoding="utf-8")
    factory_text = (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "context.py").read_text(encoding="utf-8")

    assert "RuntimeContextFactory" in kernel_exports
    assert "RuntimeContextFactory" in loop_text
    assert "MarketAccess" not in loop_text
    assert "MarketRequestService" not in loop_text
    assert "StrategyContext(" not in loop_text
    assert "StrategyContext(" in factory_text


def test_runtime_kernel_delegates_domain_dispatch_mapping() -> None:
    kernel_exports = __import__("kairospy.application.runtime.kernel", fromlist=["__all__"]).__all__
    engine_text = (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "engine.py").read_text(encoding="utf-8")
    dispatcher_text = (ROOT / "kairospy" / "application" / "runtime" / "kernel" / "dispatcher.py").read_text(encoding="utf-8")

    assert "hook_for_domain" in kernel_exports
    assert "phase_for_domain" in kernel_exports
    assert "hook_for_domain(envelope.domain)" in engine_text
    assert "phase_for_domain(envelope.domain)" in engine_text
    assert "def _hook_for_domain" not in engine_text
    assert "def _phase_for_domain" not in engine_text
    assert "on_account" in dispatcher_text
    assert "on_order" in dispatcher_text


def test_strategy_run_result_models_live_outside_kernel_exports() -> None:
    kernel_exports = __import__("kairospy.application.runtime.kernel", fromlist=["__all__"]).__all__
    model_exports = __import__("kairospy.application.runtime.model", fromlist=["__all__"]).__all__
    runtime_exports = __import__("kairospy.application.runtime", fromlist=["__all__"]).__all__

    assert "StrategyCallbackRecord" not in kernel_exports
    assert "StrategyRunResult" not in kernel_exports
    assert "StrategyCallbackRecord" in model_exports
    assert "StrategyRunResult" in model_exports
    assert "StrategyCallbackRecord" in runtime_exports
    assert "StrategyRunResult" in runtime_exports


def test_runtime_data_pipeline_lives_in_kernel_layer() -> None:
    kernel_exports = __import__("kairospy.application.runtime.kernel", fromlist=["__all__"]).__all__
    model_exports = __import__("kairospy.application.runtime.model", fromlist=["__all__"]).__all__
    runtime_exports = __import__("kairospy.application.runtime", fromlist=["__all__"]).__all__

    assert "RuntimeDataPipeline" in kernel_exports
    assert "RuntimeDataPipeline" not in model_exports
    assert "RuntimeDataPipeline" not in runtime_exports


def test_runtime_top_level_exports_do_not_expose_kernel_or_projection_internals() -> None:
    runtime_exports = set(__import__("kairospy.application.runtime", fromlist=["__all__"]).__all__)

    internal_names = {
        "RuntimeKernel",
        "RuntimeKernelSession",
        "RuntimeEngine",
        "RuntimeRunFrame",
        "RuntimeState",
        "RuntimeDataPipeline",
        "RuntimeRequestProviders",
        "RuntimeProjectionRegistry",
        "RuntimeProjection",
        "RuntimeComponent",
        "MarketProjection",
        "MarketState",
        "MarketStore",
        "MarketViewPublisher",
    }
    assert runtime_exports.isdisjoint(internal_names)


def test_paper_mode_does_not_rewrite_backtest_engine_identity_after_construction() -> None:
    text = (ROOT / "kairospy" / "application" / "mode" / "paper" / "engine.py").read_text(encoding="utf-8")
    backtest_text = (ROOT / "kairospy" / "application" / "mode" / "backtest" / "engine.py").read_text(encoding="utf-8")

    assert "._engine.runtime_mode =" not in text
    assert "._engine.account =" not in text
    assert "runtime_mode=self.runtime_mode" in text
    assert "BacktestEngine" not in text
    assert "SimulatedRunAdapter" in text
    assert "kairospy.application.mode.backtest.engine" not in text
    assert "SimulatedRunAdapter" in backtest_text
    assert "class BacktestEngine" in backtest_text
    assert "SimulatedRunEngine" not in backtest_text
    assert "_record_equity" not in backtest_text


def test_service_layer_owns_account_and_reference_orchestration_exports() -> None:
    account_exports = __import__("kairospy.core.account", fromlist=["__all__"]).__all__
    reference_exports = __import__("kairospy.core.reference", fromlist=["__all__"]).__all__
    service_exports = __import__("kairospy.application.service", fromlist=["__all__"]).__all__
    service_account_exports = __import__("kairospy.application.service.domains.account", fromlist=["__all__"]).__all__
    service_market_exports = __import__("kairospy.application.service.domains.market", fromlist=["__all__"]).__all__
    service_reference_exports = __import__("kairospy.application.service.domains.reference", fromlist=["__all__"]).__all__

    assert "bootstrap_account" not in account_exports
    assert "AccountBootstrapResult" not in account_exports
    assert "AccountDifference" not in account_exports
    assert "AccountGateway" not in account_exports
    assert "AccountProjection" not in account_exports
    assert "BuyingPowerCheck" not in account_exports
    assert "CashBuyingPowerModel" not in account_exports
    assert "MarginBuyingPowerModel" not in account_exports
    assert "Reservation" not in account_exports
    assert "ReservationBook" not in account_exports
    assert "ReservationStatus" not in account_exports
    assert "compare_account_state" not in account_exports
    assert "project_account" not in account_exports
    assert "reserve_cash_order" not in account_exports
    assert "AccountState" in account_exports
    assert "derive_account_state" in account_exports
    assert "SnapshotAccountGateway" not in account_exports
    assert "ReferenceRefreshResult" not in reference_exports
    assert "ReferenceRefreshService" not in reference_exports
    assert "ReferenceStore" not in reference_exports
    assert "CorporateActionService" not in reference_exports
    assert "ReferenceCatalogTransition" not in reference_exports
    assert "UniverseSelector" not in reference_exports
    assert "apply_catalog_snapshot" not in reference_exports
    assert "catalog_from_market_rows" not in reference_exports
    assert "ReferenceSnapshot" not in reference_exports
    assert not (ROOT / "kairospy" / "core" / "reference" / "transition.py").exists()
    assert not (ROOT / "kairospy" / "core" / "reference" / "corporate_actions.py").exists()
    assert not (ROOT / "kairospy" / "core" / "reference" / "serde.py").exists()
    assert not (ROOT / "kairospy" / "core" / "reference" / "builders.py").exists()
    assert not (ROOT / "kairospy" / "core" / "market" / "records.py").exists()
    assert not (ROOT / "kairospy" / "core" / "account" / "projection.py").exists()
    assert not (ROOT / "kairospy" / "core" / "account" / "reservation.py").exists()
    assert not (ROOT / "kairospy" / "core" / "account" / "emulation.py").exists()
    assert not (ROOT / "kairospy" / "core" / "execution" / "simulation.py").exists()
    assert not (ROOT / "kairospy" / "core" / "execution" / "live.py").exists()
    execution_exports = __import__("kairospy.core.execution", fromlist=["__all__"]).__all__
    service_execution_exports = __import__("kairospy.application.service.domains.execution", fromlist=["__all__"]).__all__
    assert "JsonExecutionStateStore" not in execution_exports
    assert "LiveExecutionAdapter" not in execution_exports
    assert "SimulatedExecutionAdapter" not in execution_exports
    assert "BuyingPowerCheck" in execution_exports
    assert "ReservationBook" in execution_exports
    assert "ReservationStatus" in execution_exports
    market_exports = __import__("kairospy.core.market", fromlist=["__all__"]).__all__
    assert "bind_market_data" not in market_exports
    assert "MarketDataBinding" not in market_exports
    assert service_exports == []
    assert "JsonExecutionStateStore" in service_execution_exports
    assert "LiveExecutionAdapter" in service_execution_exports
    assert "SimulatedExecutionAdapter" in service_execution_exports
    assert "bind_market_data" in service_market_exports
    assert "MarketDataBinding" in service_market_exports
    assert "bootstrap_account" in service_account_exports
    assert "AccountDifference" in service_account_exports
    assert "compare_account_state" in service_account_exports
    assert "SnapshotAccountGateway" in service_account_exports
    assert "ReferenceRefreshService" in service_reference_exports
    assert "ReferenceStore" in service_reference_exports
    assert "ReferenceDataRefreshService" in service_reference_exports
    assert "ReferenceRefreshResult" in service_reference_exports
    assert "ReferenceCatalogTransition" in service_reference_exports
    assert "UniverseSelector" in service_reference_exports
    assert "CorporateActionService" in service_reference_exports
    assert "apply_catalog_snapshot" in service_reference_exports
    assert "InstrumentProviderRefreshService" in service_reference_exports
    assert "catalog_from_market_rows" in service_reference_exports
    assert "ReferenceSnapshot" in service_reference_exports
    assert "LivePrivateStreamCollector" in service_account_exports
    assert "replay_rows" in service_market_exports
    service_run_exports = __import__("kairospy.application.service.operations.run", fromlist=["__all__"]).__all__
    assert "AccountRegistry" in service_run_exports
    assert "RunAccountJournal" in service_run_exports
    assert not (ROOT / "kairospy" / "application" / "service" / "operations" / "accounts.py").exists()
    assert not (ROOT / "kairospy" / "application" / "service" / "operations" / "journal.py").exists()
    assert "configured_backtest" in __import__("kairospy.application.service.modes.backtest", fromlist=["__all__"]).__all__
    assert "configured_event_mode" in __import__("kairospy.application.service.operations.run", fromlist=["__all__"]).__all__
    assert not (ROOT / "kairospy" / "infrastructure" / "integrations" / "reference.py").exists()
    assert not (ROOT / "kairospy" / "infrastructure" / "integrations" / "binance_lifecycle.py").exists()
    assert not (ROOT / "kairospy" / "infrastructure" / "integrations" / "massive_lifecycle.py").exists()
    assert not (ROOT / "kairospy" / "application" / "mode" / "live" / "private_stream.py").exists()


def test_reference_owns_exchange_broker_provider_definitions() -> None:
    core = __import__("kairospy.core", fromlist=["__all__"])
    core_exports = __import__("kairospy.core", fromlist=["__all__"]).__all__
    reference = __import__("kairospy.core.reference", fromlist=["__all__"])
    reference_exports = __import__("kairospy.core.reference", fromlist=["__all__"]).__all__
    integration_exports = __import__("kairospy.infrastructure.integrations", fromlist=["__all__"]).__all__

    assert {"Exchange", "Broker", "Provider"}.isdisjoint(core_exports)
    assert {"NYSE", "NASDAQ", "BINANCE", "HYPERLIQUID", "OKX"}.isdisjoint(core_exports)
    assert {"Exchange", "Broker", "Provider"}.issubset(reference_exports)
    assert {"NYSE", "NASDAQ", "BINANCE", "HYPERLIQUID", "OKX"}.issubset(reference_exports)
    assert "BINANCE_BROKER" not in core_exports
    assert "IBKR_BROKER" not in core_exports
    assert "MASSIVE_PROVIDER" not in core_exports
    assert not (ROOT / "kairospy" / "core" / "participants.py").exists()
    assert reference.OKEX is reference.OKX
    assert reference.NYSE.metadata["mic"] == "XNYS"
    assert reference.NASDAQ.metadata["mic"] == "XNAS"
    assert {"Broker", "Provider", "InstrumentProvider", "Nasdaq", "Nyse", "Okx", "Okex"}.isdisjoint(integration_exports)
    assert {"BrokerClient", "ReferenceDataClient", "HistoricalMarketDataClient", "LiveMarketDataFeed"}.issubset(
        integration_exports
    )


def test_integration_connectors_are_grouped_by_counterparty_kind() -> None:
    connectors = ROOT / "kairospy" / "infrastructure" / "integrations" / "connectors"

    assert (connectors / "exchange" / "binance" / "market_data.py").exists()
    assert (connectors / "exchange" / "binance" / "broker.py").exists()
    assert (connectors / "exchange" / "hyperliquid" / "market_data.py").exists()
    assert (connectors / "provider" / "massive.py").exists()
    assert (connectors / "broker" / "ibkr.py").exists()
    assert not (connectors / "broker" / "binance.py").exists()
    assert not (ROOT / "kairospy" / "infrastructure" / "integrations" / "brokers" / "__init__.py").exists()
    assert not (ROOT / "kairospy" / "infrastructure" / "integrations" / "providers" / "__init__.py").exists()
    assert not (connectors / "binance").exists()
    assert not (connectors / "hyperliquid").exists()


def test_surface_products_do_not_own_runtime_configuration_assembly() -> None:
    forbidden = (
        "from kairospy.infrastructure.data import DataStore",
        "from kairospy.application.context import DataContext",
        "IterableEventSource",
        "DataViewEventSource",
        "AsyncIterableEventSource",
        "import importlib",
        "sys.path",
    )
    offenders = []
    for path in (ROOT / "kairospy" / "surface" / "products").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for item in forbidden:
            if item in text:
                offenders.append(f"{path.relative_to(ROOT).as_posix()} owns {item}")
    assert offenders == []


def test_runtime_and_product_code_do_not_import_deprecated_trading_boundary() -> None:
    deprecated_package = "kairospy." + "trading"
    deprecated_coordinator = "Trading" + "Coordinator"
    searched_roots = (
        ROOT / "kairospy",
        ROOT / "examples",
        ROOT / "docs",
    )
    offenders = []
    for root in searched_roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".md"}:
                continue
            text = path.read_text(encoding="utf-8")
            if deprecated_package in text or deprecated_coordinator in text:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_accounts_boundary_does_not_import_provider_payload_code() -> None:
    offenders = []
    for path in (ROOT / "kairospy" / "core" / "account").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "kairospy.infrastructure.integrations" in text or "ccxt" in text.lower():
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_live_boundary_uses_payload_adapters_instead_of_provider_imports() -> None:
    offenders = []
    for path in (ROOT / "kairospy" / "application" / "mode" / "live").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "kairospy.infrastructure.integrations" in text or "ccxt" in text.lower():
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_reference_boundary_does_not_import_runtime_or_provider_layers() -> None:
    forbidden = (
        "kairospy.core.account",
        "kairospy.infrastructure.data",
        "kairospy.core.execution",
        "kairospy.infrastructure.integrations",
        "kairospy.application.mode.live",
        "kairospy.application.runtime",
    )
    offenders = []
    for path in (ROOT / "kairospy" / "core" / "reference").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(item in text for item in forbidden):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_data_reference_and_integration_boundaries_do_not_import_context_layer() -> None:
    offenders = []
    for root in (
        ROOT / "kairospy" / "infrastructure" / "data",
        ROOT / "kairospy" / "core" / "reference",
        ROOT / "kairospy" / "infrastructure" / "integrations",
    ):
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "from kairospy.application.context" in text or "import kairospy.application.context" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_schema_boundary_is_removed_in_favor_of_market_and_runtime_models() -> None:
    assert not (ROOT / "kairospy" / "schema").exists()

    market_root = ROOT / "kairospy" / "core" / "market"
    forbidden = ("class Instrument", "InstrumentRegistry", "from kairospy.application.context")
    offenders = []
    for path in market_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(item in text for item in forbidden):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_data_boundary_does_not_own_market_domain_models() -> None:
    forbidden = (
        "class Quote",
        "class OrderBookSnapshot",
        "class TradePrint",
        "class Bar",
        "class MarketObservation",
        "ticker_record",
        "orderbook_record",
    )
    offenders = []
    for path in (ROOT / "kairospy" / "infrastructure" / "data").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(item in text for item in forbidden):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_context_boundary_does_not_export_market_specific_data_views() -> None:
    context_init = (ROOT / "kairospy" / "application" / "context" / "__init__.py").read_text(encoding="utf-8")
    context_data = (ROOT / "kairospy" / "application" / "context" / "data.py").read_text(encoding="utf-8")
    forbidden = (
        "InstrumentDataView",
        "MarketDataView",
        "class MarketData",
        "def for_market",
        "MarketResolver",
        "self.markets",
        "markets:",
    )
    for item in forbidden:
        assert item not in context_init
        assert item not in context_data


def test_strategy_context_is_owned_by_context_layer() -> None:
    assert StrategyContext.__module__ == "kairospy.application.context.strategy"
    assert ControlRequestKind.__module__ == "kairospy.application.context.control"
    assert not (ROOT / "kairospy" / "application" / "strategy" / "control.py").exists()

    strategy_protocol = (ROOT / "kairospy" / "application" / "strategy" / "protocol.py").read_text(encoding="utf-8")
    context_strategy = (ROOT / "kairospy" / "application" / "context" / "strategy.py").read_text(encoding="utf-8")
    strategy_init = (ROOT / "kairospy" / "application" / "strategy" / "__init__.py").read_text(encoding="utf-8")
    assert "class StrategyContext" not in strategy_protocol
    assert "from kairospy.application.context import Context, StrategyContext" in strategy_protocol
    assert "from kairospy.application.context import ControlFactory, ControlJournal, ControlRequest, ControlRequestKind" in strategy_init
    top_level_imports = tuple(
        line
        for line in context_strategy.splitlines()
        if line.startswith("from ") or line.startswith("import ")
    )
    assert all("kairospy.application.strategy.events" not in line for line in top_level_imports)
    assert all("kairospy.application.strategy.views" not in line for line in top_level_imports)
    assert all("kairospy.application.strategy.control" not in line for line in top_level_imports)

    offenders = []
    for root in (ROOT / "tests", ROOT / "examples"):
        if not root.exists():
            continue
        for file in root.rglob("*.py"):
            for line in file.read_text(encoding="utf-8").splitlines():
                if line.startswith("from kairospy.application.strategy import") and "StrategyContext" in line:
                    offenders.append(file.relative_to(ROOT).as_posix())
                    break
    assert offenders == []


def test_standard_market_records_use_explicit_market_identity_fields() -> None:
    row = ticker_record(
        venue="binance",
        market="spot",
        instrument="BTC/USDT",
        ticker={"timestamp": 1767225600000, "bid": "100", "ask": "101"},
    )

    assert row["market_id"] == "market:binance:spot:btc_usdt"
    assert row["instrument_id"] == "instrument:spot:btc:usdt"
    assert row["market_key"] == "binance_spot_btc_usdt"
    assert "instrumentId" not in row


def test_core_runtime_paths_do_not_read_legacy_instrument_id_field() -> None:
    offenders = []
    for path in (
        ROOT / "kairospy" / "application" / "runtime",
        ROOT / "kairospy" / "application" / "mode" / "backtest",
    ):
        for file in path.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            text = file.read_text(encoding="utf-8")
            if "instrumentId" in text:
                offenders.append(file.relative_to(ROOT).as_posix())
    assert offenders == []


def test_runtime_data_pipeline_has_no_legacy_event_compatibility_layer() -> None:
    assert not (ROOT / "kairospy" / "application" / "runtime" / "events.py").exists()

    envelope_fields = RuntimeDataEnvelope.__dataclass_fields__
    assert set(envelope_fields) == {"domain", "kind", "time", "sequence", "payload", "stream", "source", "metadata"}

    runtime_exports = __import__("kairospy.application.runtime", fromlist=["__all__"]).__all__
    forbidden_exports = {
        "AccountRuntimeEvent",
        "ClockEvent",
        "OrderRuntimeEvent",
        "RuntimeEvent",
        "SystemRuntimeEvent",
        "envelope_from_runtime_event",
        "parse_event_time",
    }
    assert forbidden_exports.isdisjoint(runtime_exports)

    forbidden_terms = (
        "AccountRuntimeEvent",
        "ClockEvent",
        "OrderRuntimeEvent",
        "RuntimeEvent",
        "SystemRuntimeEvent",
        "envelope_from_runtime_event",
        "ingest_event",
        "ingest_record",
        "raw_event",
    )
    offenders = []
    for root in (
        ROOT / "kairospy" / "application" / "runtime",
        ROOT / "kairospy" / "application" / "mode",
    ):
        for file in root.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            text = file.read_text(encoding="utf-8")
            if any(term in text for term in forbidden_terms):
                offenders.append(file.relative_to(ROOT).as_posix())
    assert offenders == []


def test_strategy_signal_has_no_business_payload() -> None:
    signal_fields = StrategySignal.__dataclass_fields__
    assert set(signal_fields) == {"domain", "kind", "time", "sequence", "stream", "source", "metadata"}

    strategy_exports = __import__("kairospy.application.strategy", fromlist=["__all__"]).__all__
    forbidden_exports = {
        "AccountChange",
        "ClockChange",
        "MarketChange",
        "OrderChange",
        "StrategyChange",
        "StrategyEvent",
        "SystemChange",
    }
    assert forbidden_exports.isdisjoint(strategy_exports)
    assert "StrategySignal" in strategy_exports


def test_execution_and_backtest_do_not_read_callback_signal_payload_directly() -> None:
    forbidden = (
        "context.event.payload",
        'getattr(context.event, "payload"',
        'context.latest_data(domain="market")',
        "context.latest_data(domain='market')",
    )
    offenders = []
    for path in (
        ROOT / "kairospy" / "core" / "execution",
        ROOT / "kairospy" / "application" / "mode" / "backtest",
    ):
        for file in path.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            text = file.read_text(encoding="utf-8")
            if any(item in text for item in forbidden):
                offenders.append(file.relative_to(ROOT).as_posix())
    assert offenders == []


def test_project_sources_examples_and_tests_use_market_data_binding_api() -> None:
    offenders = []
    legacy_call = ".for_" + "instrument("
    for path in (ROOT / "kairospy", ROOT / "examples", ROOT / "tests"):
        for file in path.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            text = file.read_text(encoding="utf-8")
            if legacy_call in text:
                offenders.append(file.relative_to(ROOT).as_posix())
    assert offenders == []
