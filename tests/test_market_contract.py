from decimal import Decimal
from pathlib import Path
import subprocess

import pytest

from kairospy.infrastructure.contracts.market import (
    MarketIndexedViewQueries,
    MarketQuoteCurrent,
    MarketViewKey,
    MarketViewKind,
    market_indexed_environment_path,
)
from kairospy.investment.apps.market.application.mapping import map_market_view


def test_market_view_key_matches_rust_contract_encoding() -> None:
    key = MarketViewKey(
        scope_key="market:binance:spot:BTCUSDT",
        provider="binance",
        kind=MarketViewKind.BAR,
        qualifier="1m",
    )

    assert key.canonical_key() == (
        "scope=market:binance:spot:BTCUSDT;provider=binance;view=bar;qualifier=1m"
    )
    assert str(market_indexed_environment_path("/tmp/workspace")) == (
        "/tmp/workspace/views/v3/Market/market-main/epoch-1/current.lmdb"
    )


def test_market_view_key_rejects_incomplete_identity() -> None:
    with pytest.raises(ValueError, match="indexed view identity is invalid"):
        MarketViewKey("", "binance", MarketViewKind.QUOTE)


def test_rust_market_publisher_value_is_readable_by_kairospy(tmp_path: Path) -> None:
    subprocess.run(
        [
            "cargo",
            "run",
            "--quiet",
            "-p",
            "kairos-market-contract",
            "--example",
            "write_indexed_quote_fixture",
            "--",
            str(tmp_path),
        ],
        check=True,
    )
    queries = MarketIndexedViewQueries(
        tmp_path,
        workspace_id="workspace",
        launch_id="launch",
        instance_id="instance",
    )
    frame = queries.read(
        MarketViewKey("market:fixture", "fixture", MarketViewKind.QUOTE)
    )

    assert frame is not None
    assert frame.metadata.applied_event_sequence == 41
    assert isinstance(frame.value, MarketQuoteCurrent)
    queries.close()
    # The typed result is Python-owned and does not retain an LMDB transaction.
    assert frame.value.instrument_id == "instrument:fixture"
    assert frame.value.bid_price == Decimal("123.45")
    quote = map_market_view(frame.value, kind="quote")
    assert quote.bid_price == Decimal("123.45")
