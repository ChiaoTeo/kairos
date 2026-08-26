from pathlib import Path
import subprocess

import pytest

from kairospy.infrastructure.contracts.market import (
    MarketIndexedViewQueries,
    MarketViewKey,
    MarketViewKind,
    market_indexed_environment_path,
)


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
    assert key.encoded() == (
        b"\x01\x00\x1bmarket:binance:spot:BTCUSDT\x00\x07binance\x00\x021m"
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
    assert frame.payload[4:8] == b"MQC3"
    assert frame.value.InstrumentId() == b"instrument:fixture"
    assert frame.value.BidPrice().Mantissa() == 12345
