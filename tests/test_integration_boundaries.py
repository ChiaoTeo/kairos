from __future__ import annotations

import kairospy.infrastructure.integrations as integrations
import kairospy.infrastructure.integrations.connectors as connectors
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


def test_application_common_integration_factories_delegate_to_resolver() -> None:
    path = ROOT / "kairospy" / "application" / "service" / "modes" / "common" / "integrations.py"
    text = path.read_text(encoding="utf-8")

    assert "DEFAULT_INTEGRATION_RESOLVER" in text
    assert "BinanceBroker" not in text
    assert "BinanceMarketDataConnector" not in text


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
