from __future__ import annotations

from kairospy.reference import MarketRef
from kairospy.schema.records import orderbook_record


def test_market_ref_carries_market_identity_and_records_carry_quote_fields() -> None:
    market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")
    row = orderbook_record(
        venue="binance",
        market="spot",
        instrument=market,
        book={"bids": [["100", "1.2"]], "asks": [["101", "0.8"]]},
    )

    assert market.identity_fields() == {
        "market_id": "market:binance:spot:btc_usdt",
        "instrument_id": "instrument:spot:btc:usdt",
        "market_key": "binance_spot_btc_usdt",
        "venue": "binance",
        "market": "spot",
        "source_symbol": "BTC/USDT",
    }
    assert row["bid1"] == "100"
    assert row["bid1_size"] == "1.2"
    assert row["ask1"] == "101"
    assert row["ask1_size"] == "0.8"
    assert row["bid_depth"] == 1
    assert row["ask_depth"] == 1
    assert row["bids"] == [["100", "1.2"]]
    assert row["asks"] == [["101", "0.8"]]
    assert row["nonce"] is None
