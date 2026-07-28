from __future__ import annotations

from kairospy.service.domains.market.records import orderbook_record


def test_orderbook_record_accepts_missing_depth_and_keeps_flat_best_quote_fields() -> None:
    row = orderbook_record(
        venue="simulated",
        instrument="BTC/USDT",
        book={"timestamp": 1767225600000, "bids": [["100", "1.2"]]},
    )

    assert row["time"] == "2026-01-01T00:00:00+00:00"
    assert row["kind"] == "orderbook"
    assert row["venue"] == "simulated"
    assert row["market"] == "spot"
    assert row["market_key"] == "simulated_spot_btc_usdt"
    assert row["source_symbol"] == "BTC/USDT"
    assert row["bid1"] == "100"
    assert row["bid1_size"] == "1.2"
    assert row["ask1"] is None
    assert row["ask1_size"] is None
    assert row["bid_depth"] == 1
    assert row["ask_depth"] == 0
    assert row["bids"] == [["100", "1.2"]]
    assert row["asks"] == []


def test_orderbook_record_accepts_preflattened_simulated_quotes() -> None:
    row = orderbook_record(
        venue="simulated",
        instrument="BTC/USDT",
        book={
            "timestamp": 1767225600000,
            "bid": "100",
            "bid_size": "1.2",
            "ask": "101",
            "ask_size": "0.8",
        },
    )

    assert row["market_key"] == "simulated_spot_btc_usdt"
    assert row["bid1"] == "100"
    assert row["bid1_size"] == "1.2"
    assert row["ask1"] == "101"
    assert row["ask1_size"] == "0.8"
    assert row["bid_depth"] == 1
    assert row["ask_depth"] == 1
    assert row["bids"] == []
    assert row["asks"] == []
