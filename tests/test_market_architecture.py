from __future__ import annotations

from pathlib import Path

from kairospy.application.usecases.market.application.component import MarketApplication
from kairospy.application.usecases.market.application.requests import MarketDataKind, MarketDataRow, MarketDataSpec
from kairospy.application.usecases.market.protocol import MarketDataReader, MarketDataStore, MarketDataWriter, MarketHistoricalClient


ROOT = Path(__file__).parents[1]


def test_market_public_application_contracts_do_not_use_generic_objects() -> None:
    for relative in (
        "kairospy/application/usecases/market/application/component.py",
        "kairospy/application/usecases/market/protocol.py",
        "kairospy/application/usecases/market/application/integration.py",
        "kairospy/application/usecases/market/application/requests.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert ": object" not in source
        assert "-> object" not in source
        assert "**kwargs" not in source


def test_market_callers_do_not_import_domain_or_private_services() -> None:
    for path in ROOT.joinpath("kairospy").rglob("*.py"):
        if "application/usecases/market/" in str(path):
            continue
        source = path.read_text(encoding="utf-8")
        assert "application.usecases.market.services" not in source
        assert "application.usecases.market.domain" not in source


def test_market_facade_owns_business_entrypoints() -> None:
    market = MarketApplication()
    assert hasattr(market, "read")
    assert hasattr(market, "download")
    assert hasattr(market, "subscribe")
    assert hasattr(market, "handle_event")
    assert not hasattr(market, "queries")
    assert not hasattr(market, "ingestion")
    assert not hasattr(market, "subscriptions_service")


def test_market_contract_types_are_available_and_semantic() -> None:
    assert MarketDataKind.OHLCV.value == "ohlcv"
    assert MarketDataSpec.__annotations__["kind"] == "MarketDataKind | str"
    assert "time" in MarketDataRow.__annotations__
    assert MarketDataReader is not None
    assert MarketDataStore is not None
    assert MarketDataWriter is not None
    assert MarketHistoricalClient is not None
