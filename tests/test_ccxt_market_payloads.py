from __future__ import annotations

from datetime import timezone

from kairospy.infrastructure.integrations.payloads.ccxt_market import ccxt_order_book_delta, ccxt_order_book_update, ccxt_trade_update
from kairospy.core.reference import MarketRef


def test_ccxt_realtime_orderbook_update_accepts_missing_timestamp() -> None:
    market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")

    event = ccxt_order_book_update(
        {
            "bids": [[100, 1]],
            "asks": [[101, 2]],
            "nonce": 1,
        },
        market=market,
    )

    assert event.observed_at.tzinfo is timezone.utc
    assert event.value.bid1.price == 100


def test_ccxt_realtime_trade_update_accepts_missing_timestamp() -> None:
    market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")

    event = ccxt_trade_update(
        {
            "id": "t1",
            "side": "buy",
            "price": "100",
            "amount": "0.1",
        },
        market=market,
    )

    assert event.observed_at.tzinfo is timezone.utc
    assert event.value.price == 100


def test_ccxt_binance_depth_update_parses_orderbook_delta() -> None:
    market = MarketRef.ephemeral(venue="binance", market="spot", source_symbol="BTC/USDT")

    delta = ccxt_order_book_delta(
        {
            "E": 1767225600000,
            "U": 101,
            "u": 102,
            "b": [["100", "1.5"]],
            "a": [["101", "0"]],
        },
        market=market,
    )

    assert delta.first_nonce == 101
    assert delta.last_nonce == 102
    assert delta.nonce == 102
    assert len(delta.changes) == 2
    assert delta.changes[0].side == "bid"
