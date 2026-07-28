from __future__ import annotations

from pathlib import Path

from kairospy.core.execution import ExecutionCoordinator
from kairospy.service.domains.execution import LiveExecutionAdapter, SimulatedExecutionAdapter
from kairospy.service.domains.market.records import ticker_record
from kairospy.context import ControlRequestKind, StrategyContext
from kairospy.runtime import RuntimeDataEnvelope
from kairospy.strategy import StrategySignal


ROOT = Path(__file__).resolve().parents[1]


def test_deprecated_trading_package_is_removed() -> None:
    assert not (ROOT / "kairospy" / "trading").exists()


def test_top_level_package_layout_matches_current_architecture() -> None:
    allowed = {
        "context",
        "core",
        "data",
        "integrations",
        "modes",
        "service",
        "runtime",
        "strategy",
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
    audit = ROOT / "docs" / "migration_audit.md"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert architecture.exists()
    assert audit.exists()
    assert "docs/architecture.md" in readme
    assert "docs/migration_audit.md" in readme


def test_execution_domain_owns_execution_names() -> None:
    assert ExecutionCoordinator.__module__ == "kairospy.core.execution.coordinator"
    assert LiveExecutionAdapter.__module__ == "kairospy.service.domains.execution.live"
    assert SimulatedExecutionAdapter.__module__ == "kairospy.service.domains.execution.simulation"


def test_architecture_dependency_direction_is_enforced() -> None:
    forbidden_by_root = {
        ROOT / "kairospy" / "core": (
            "kairospy.strategy",
            "kairospy.runtime",
            "kairospy.modes",
            "kairospy.integrations",
            "kairospy.surface",
        ),
        ROOT / "kairospy" / "strategy": (
            "kairospy.runtime",
            "kairospy.modes",
            "kairospy.integrations",
            "kairospy.surface",
        ),
        ROOT / "kairospy" / "runtime": (
            "kairospy.integrations",
            "kairospy.modes",
            "kairospy.surface",
        ),
        ROOT / "kairospy" / "integrations": (
            "kairospy.runtime",
            "kairospy.modes",
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


def test_service_layer_owns_account_and_reference_orchestration_exports() -> None:
    account_exports = __import__("kairospy.core.account", fromlist=["__all__"]).__all__
    reference_exports = __import__("kairospy.core.reference", fromlist=["__all__"]).__all__
    service_exports = __import__("kairospy.service", fromlist=["__all__"]).__all__
    service_account_exports = __import__("kairospy.service.domains.account", fromlist=["__all__"]).__all__
    service_market_exports = __import__("kairospy.service.domains.market", fromlist=["__all__"]).__all__
    service_reference_exports = __import__("kairospy.service.domains.reference", fromlist=["__all__"]).__all__

    assert "bootstrap_account" not in account_exports
    assert "AccountBootstrapResult" not in account_exports
    assert "SnapshotAccountGateway" not in account_exports
    assert "ReferenceRefreshResult" not in reference_exports
    assert "ReferenceRefreshService" not in reference_exports
    assert "ReferenceStore" not in reference_exports
    assert "CorporateActionService" not in reference_exports
    assert "catalog_from_market_rows" not in reference_exports
    assert "ReferenceSnapshot" not in reference_exports
    assert not (ROOT / "kairospy" / "core" / "reference" / "builders.py").exists()
    assert not (ROOT / "kairospy" / "core" / "market" / "records.py").exists()
    assert not (ROOT / "kairospy" / "core" / "execution" / "simulation.py").exists()
    assert not (ROOT / "kairospy" / "core" / "execution" / "live.py").exists()
    execution_exports = __import__("kairospy.core.execution", fromlist=["__all__"]).__all__
    service_execution_exports = __import__("kairospy.service.domains.execution", fromlist=["__all__"]).__all__
    assert "JsonExecutionStateStore" not in execution_exports
    assert "LiveExecutionAdapter" not in execution_exports
    assert "SimulatedExecutionAdapter" not in execution_exports
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
    assert "SnapshotAccountGateway" in service_account_exports
    assert "ReferenceRefreshService" in service_reference_exports
    assert "ReferenceStore" in service_reference_exports
    assert "ReferenceDataRefreshService" in service_reference_exports
    assert "ReferenceRefreshResult" in service_reference_exports
    assert "InstrumentProviderRefreshService" in service_reference_exports
    assert "catalog_from_market_rows" in service_reference_exports
    assert "ReferenceSnapshot" in service_reference_exports
    assert "LivePrivateStreamCollector" in service_account_exports
    assert "replay_rows" in service_market_exports
    assert "configured_backtest" in __import__("kairospy.service.modes.backtest", fromlist=["__all__"]).__all__
    assert "configured_event_mode" in __import__("kairospy.service.operations.run", fromlist=["__all__"]).__all__
    assert not (ROOT / "kairospy" / "integrations" / "reference.py").exists()
    assert not (ROOT / "kairospy" / "integrations" / "binance_lifecycle.py").exists()
    assert not (ROOT / "kairospy" / "integrations" / "massive_lifecycle.py").exists()
    assert not (ROOT / "kairospy" / "modes" / "live" / "private_stream.py").exists()


def test_core_owns_exchange_broker_provider_definitions() -> None:
    core = __import__("kairospy.core", fromlist=["__all__"])
    core_exports = __import__("kairospy.core", fromlist=["__all__"]).__all__
    integration_exports = __import__("kairospy.integrations", fromlist=["__all__"]).__all__

    assert {"Exchange", "Broker", "Provider"}.issubset(core_exports)
    assert {"NYSE", "NASDAQ", "BINANCE", "HYPERLIQUID", "OKX"}.issubset(core_exports)
    assert "BINANCE_BROKER" not in core_exports
    assert "IBKR_BROKER" not in core_exports
    assert "MASSIVE_PROVIDER" not in core_exports
    assert core.OKEX is core.OKX
    assert core.NYSE.metadata["mic"] == "XNYS"
    assert core.NASDAQ.metadata["mic"] == "XNAS"
    assert {"Broker", "Provider", "InstrumentProvider", "Nasdaq", "Nyse", "Okx", "Okex"}.isdisjoint(integration_exports)
    assert {"BrokerClient", "ReferenceDataClient", "HistoricalMarketDataClient", "LiveMarketDataFeed"}.issubset(
        integration_exports
    )


def test_integration_connectors_are_grouped_by_counterparty_kind() -> None:
    connectors = ROOT / "kairospy" / "integrations" / "connectors"

    assert (connectors / "exchange" / "binance" / "market_data.py").exists()
    assert (connectors / "exchange" / "binance" / "broker.py").exists()
    assert (connectors / "exchange" / "hyperliquid" / "market_data.py").exists()
    assert (connectors / "provider" / "massive.py").exists()
    assert (connectors / "broker" / "ibkr.py").exists()
    assert not (connectors / "broker" / "binance.py").exists()
    assert not (ROOT / "kairospy" / "integrations" / "brokers" / "__init__.py").exists()
    assert not (ROOT / "kairospy" / "integrations" / "providers" / "__init__.py").exists()
    assert not (connectors / "binance").exists()
    assert not (connectors / "hyperliquid").exists()


def test_surface_products_do_not_own_runtime_configuration_assembly() -> None:
    forbidden = (
        "from kairospy.data import DataStore",
        "from kairospy.context import DataContext",
        "from kairospy.runtime import IterableEventSource",
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
        if "kairospy.integrations" in text or "ccxt" in text.lower():
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_live_boundary_uses_payload_adapters_instead_of_provider_imports() -> None:
    offenders = []
    for path in (ROOT / "kairospy" / "modes" / "live").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "kairospy.integrations" in text or "ccxt" in text.lower():
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_reference_boundary_does_not_import_runtime_or_provider_layers() -> None:
    forbidden = (
        "kairospy.core.account",
        "kairospy.data",
        "kairospy.core.execution",
        "kairospy.integrations",
        "kairospy.modes.live",
        "kairospy.runtime",
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
        ROOT / "kairospy" / "data",
        ROOT / "kairospy" / "core" / "reference",
        ROOT / "kairospy" / "integrations",
    ):
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "from kairospy.context" in text or "import kairospy.context" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_schema_boundary_is_removed_in_favor_of_market_and_runtime_models() -> None:
    assert not (ROOT / "kairospy" / "schema").exists()

    market_root = ROOT / "kairospy" / "core" / "market"
    forbidden = ("class Instrument", "InstrumentRegistry", "from kairospy.context")
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
    for path in (ROOT / "kairospy" / "data").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(item in text for item in forbidden):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_context_boundary_does_not_export_market_specific_data_views() -> None:
    context_init = (ROOT / "kairospy" / "context" / "__init__.py").read_text(encoding="utf-8")
    context_data = (ROOT / "kairospy" / "context" / "data.py").read_text(encoding="utf-8")
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
    assert StrategyContext.__module__ == "kairospy.context.strategy"
    assert ControlRequestKind.__module__ == "kairospy.context.control"
    assert not (ROOT / "kairospy" / "strategy" / "control.py").exists()

    strategy_protocol = (ROOT / "kairospy" / "strategy" / "protocol.py").read_text(encoding="utf-8")
    context_strategy = (ROOT / "kairospy" / "context" / "strategy.py").read_text(encoding="utf-8")
    strategy_init = (ROOT / "kairospy" / "strategy" / "__init__.py").read_text(encoding="utf-8")
    assert "class StrategyContext" not in strategy_protocol
    assert "from kairospy.context import Context, StrategyContext" in strategy_protocol
    assert "from kairospy.context import ControlFactory, ControlJournal, ControlRequest, ControlRequestKind" in strategy_init
    top_level_imports = tuple(
        line
        for line in context_strategy.splitlines()
        if line.startswith("from ") or line.startswith("import ")
    )
    assert all("kairospy.strategy.events" not in line for line in top_level_imports)
    assert all("kairospy.strategy.views" not in line for line in top_level_imports)
    assert all("kairospy.strategy.control" not in line for line in top_level_imports)

    offenders = []
    for root in (ROOT / "tests", ROOT / "examples"):
        if not root.exists():
            continue
        for file in root.rglob("*.py"):
            for line in file.read_text(encoding="utf-8").splitlines():
                if line.startswith("from kairospy.strategy import") and "StrategyContext" in line:
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
        ROOT / "kairospy" / "runtime",
        ROOT / "kairospy" / "modes" / "backtest",
    ):
        for file in path.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            text = file.read_text(encoding="utf-8")
            if "instrumentId" in text:
                offenders.append(file.relative_to(ROOT).as_posix())
    assert offenders == []


def test_runtime_data_pipeline_has_no_legacy_event_compatibility_layer() -> None:
    assert not (ROOT / "kairospy" / "runtime" / "events.py").exists()

    envelope_fields = RuntimeDataEnvelope.__dataclass_fields__
    assert set(envelope_fields) == {"domain", "kind", "time", "sequence", "payload", "stream", "source", "metadata"}

    runtime_exports = __import__("kairospy.runtime", fromlist=["__all__"]).__all__
    forbidden_exports = {
        "AccountRuntimeEvent",
        "ClockEvent",
        "MarketEvent",
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
        "MarketEvent",
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
        ROOT / "kairospy" / "runtime",
        ROOT / "kairospy" / "modes",
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

    strategy_exports = __import__("kairospy.strategy", fromlist=["__all__"]).__all__
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
        ROOT / "kairospy" / "modes" / "backtest",
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
