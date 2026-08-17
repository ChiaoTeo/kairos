from pathlib import Path

import pytest

from kairospy.infrastructure.contracts.market import (
    MarketViewKey,
    MarketViewKind,
)


def test_market_view_key_matches_rust_contract_path() -> None:
    key = MarketViewKey(
        scope_key="market:binance:spot:BTCUSDT",
        source_id="binance",
        kind=MarketViewKind.BAR,
        qualifier="1m",
    )

    assert key.canonical_key() == (
        "scope=market:binance:spot:BTCUSDT;source=binance;"
        "view=bar;qualifier=1m"
    )
    assert key.resource_id() == (
        "scope-market%3Abinance%3Aspot%3ABTCUSDT-binance-bar-1m"
    )
    assert key.resource_path("/tmp/workspace") == Path(
        "/tmp/workspace/scope-market%3Abinance%3Aspot%3ABTCUSDT-binance-bar-1m.e1.mmap"
    )


def test_market_view_key_rejects_incomplete_identity() -> None:
    with pytest.raises(ValueError, match="view identity is incomplete"):
        MarketViewKey("", "binance", MarketViewKind.QUOTE)
