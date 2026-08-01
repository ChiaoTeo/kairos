from __future__ import annotations

import kairospy.infrastructure.integrations as integrations
import kairospy.infrastructure.integrations.connectors as connectors
import kairospy.infrastructure.integrations.connectors.broker as broker_connectors
import kairospy.infrastructure.integrations.connectors.exchange as exchange_connectors
import kairospy.infrastructure.integrations.connectors.provider as provider_connectors
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_core_does_not_import_outer_layers() -> None:
    core_root = ROOT / "kairospy" / "core"
    offenders = []
    forbidden = (
        "from kairospy.application",
        "import kairospy.application",
        "from kairospy.infrastructure",
        "import kairospy.infrastructure",
        "from kairospy.surface",
        "import kairospy.surface",
    )
    for path in core_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_core_execution_does_not_expose_broker_gateway() -> None:
    execution_root = ROOT / "kairospy" / "core" / "execution"
    offenders = []
    forbidden = ("BrokerGateway", "create_order(", "broker_resolver", "broker_symbol_resolver")
    for path in execution_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_core_order_does_not_define_raw_venue_response() -> None:
    text = (ROOT / "kairospy" / "core" / "order" / "model.py").read_text(encoding="utf-8")

    assert "VenueOrderResponse" not in text
    assert "TypedDict" not in text


def test_application_services_and_runtime_do_not_import_infrastructure() -> None:
    offenders = []
    forbidden = (
        "from kairospy.infrastructure",
        "import kairospy.infrastructure",
        "from kairospy.surface",
        "import kairospy.surface",
    )
    for root in (
        ROOT / "kairospy" / "application" / "service",
        ROOT / "kairospy" / "application" / "support" / "runtime",
    ):
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                if marker in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_exchange_connectors_do_not_define_broker_execution_classes() -> None:
    exchange_root = ROOT / "kairospy" / "infrastructure" / "integrations" / "connectors" / "exchange"
    offenders = []
    for path in exchange_root.rglob("*.py"):
        if path.name == "__init__.py":
            continue
        text = path.read_text(encoding="utf-8")
        forbidden = ("class BinanceBroker", "class OkxBroker", "create_order(", "cancel_order(", "fetch_balance(")
        for marker in forbidden:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)}:{marker}")
    assert offenders == []


def test_binance_broker_has_single_connector_entrypoint() -> None:
    exchange_binance = ROOT / "kairospy" / "infrastructure" / "integrations" / "connectors" / "exchange" / "binance"
    broker_binance = ROOT / "kairospy" / "infrastructure" / "integrations" / "connectors" / "broker" / "binance"

    assert not (exchange_binance / "broker.py").exists()
    assert (broker_binance / "crypto_execution.py").exists()


def test_launch_composition_integration_factories_delegate_to_resolver() -> None:
    config_path = ROOT / "kairospy" / "application" / "support" / "launch" / "config" / "common" / "integrations.py"
    composition_path = ROOT / "kairospy" / "application" / "support" / "launch" / "composition" / "integrations.py"
    config_text = config_path.read_text(encoding="utf-8")
    composition_text = composition_path.read_text(encoding="utf-8")

    assert "DEFAULT_INTEGRATION_RESOLVER" not in config_text
    assert "DEFAULT_INTEGRATION_RESOLVER" in composition_text
    assert "BinanceBroker" not in composition_text
    assert "BinanceMarketDataConnector" not in composition_text


def test_package_roots_do_not_reexport_concrete_connectors() -> None:
    concrete_connectors = {
        "Binance",
        "BinanceBroker",
        "BinanceEquityBroker",
        "BinanceEquityMarketDataConnector",
        "BinanceEquityReferenceConnector",
        "BinanceMarketDataConnector",
        "Hyperliquid",
        "HyperliquidMarketDataConnector",
        "IBKR",
        "Massive",
        "Okx",
        "OkxBroker",
        "OkxMarketDataConnector",
    }

    assert concrete_connectors.isdisjoint(integrations.__all__)
    assert concrete_connectors.isdisjoint(connectors.__all__)
    assert concrete_connectors.isdisjoint(exchange_connectors.__all__)
    assert concrete_connectors.isdisjoint(broker_connectors.__all__)
    assert concrete_connectors.isdisjoint(provider_connectors.__all__)
