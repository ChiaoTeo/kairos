from __future__ import annotations

from pathlib import Path

from kairospy.execution import ExecutionCoordinator, LiveExecutionAdapter, SimulatedExecutionAdapter
from kairospy.schema.records import ticker_record


ROOT = Path(__file__).resolve().parents[1]


def test_deprecated_trading_package_is_removed() -> None:
    assert not (ROOT / "kairospy" / "trading").exists()


def test_top_level_package_layout_matches_current_architecture() -> None:
    allowed = {
        "accounts",
        "backtest",
        "context",
        "data",
        "execution",
        "integrations",
        "intents",
        "live",
        "orders",
        "paper",
        "reference",
        "runtime",
        "schema",
        "strategy",
        "surface",
    }
    actual = {
        path.name
        for path in (ROOT / "kairospy").iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert actual == allowed


def test_architecture_docs_cover_current_migration_boundary() -> None:
    architecture = ROOT / "docs" / "architecture.md"
    audit = ROOT / "docs" / "migration_audit.md"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert architecture.exists()
    assert audit.exists()
    assert "docs/architecture.md" in readme
    assert "docs/migration_audit.md" in readme


def test_execution_domain_owns_execution_names() -> None:
    assert ExecutionCoordinator.__module__ == "kairospy.execution.coordinator"
    assert LiveExecutionAdapter.__module__ == "kairospy.execution.live"
    assert SimulatedExecutionAdapter.__module__ == "kairospy.execution.simulation"


def test_runtime_and_product_code_do_not_import_deprecated_trading_boundary() -> None:
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
            if "kairospy.trading" in text or "TradingCoordinator" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_accounts_boundary_does_not_import_provider_payload_code() -> None:
    offenders = []
    for path in (ROOT / "kairospy" / "accounts").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "kairospy.integrations" in text or "ccxt" in text.lower():
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_live_boundary_uses_payload_adapters_instead_of_provider_imports() -> None:
    offenders = []
    for path in (ROOT / "kairospy" / "live").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "kairospy.integrations" in text or "ccxt" in text.lower():
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_reference_boundary_does_not_import_runtime_or_provider_layers() -> None:
    forbidden = (
        "kairospy.accounts",
        "kairospy.data",
        "kairospy.execution",
        "kairospy.integrations",
        "kairospy.live",
        "kairospy.runtime",
    )
    offenders = []
    for path in (ROOT / "kairospy" / "reference").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(item in text for item in forbidden):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_schema_reference_and_integration_boundaries_do_not_import_context_layer() -> None:
    offenders = []
    for root in (
        ROOT / "kairospy" / "schema",
        ROOT / "kairospy" / "reference",
        ROOT / "kairospy" / "integrations",
    ):
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if "from kairospy.context" in text or "import kairospy.context" in text:
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_schema_boundary_does_not_reintroduce_instrument_reference_models() -> None:
    schema_root = ROOT / "kairospy" / "schema"
    assert not (schema_root / "instrument.py").exists()
    assert not (schema_root / "registry.py").exists()

    forbidden = ("class Instrument", "InstrumentRegistry", "from kairospy.reference", "from kairospy.context")
    offenders = []
    for path in schema_root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if any(item in text for item in forbidden):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


def test_context_boundary_does_not_export_legacy_instrument_data_view() -> None:
    context_init = (ROOT / "kairospy" / "context" / "__init__.py").read_text(encoding="utf-8")
    context_data = (ROOT / "kairospy" / "context" / "data.py").read_text(encoding="utf-8")
    assert "InstrumentDataView" not in context_init
    assert "InstrumentDataView" not in context_data


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
        ROOT / "kairospy" / "backtest",
    ):
        for file in path.rglob("*.py"):
            if "__pycache__" in file.parts:
                continue
            text = file.read_text(encoding="utf-8")
            if "instrumentId" in text:
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
