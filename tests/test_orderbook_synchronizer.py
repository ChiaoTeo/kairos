from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kairospy.core.market import (
    OrderBookChange,
    OrderBookDelta,
    OrderBookSnapshot,
    OrderBookSyncGap,
    OrderBookSynchronizer,
    PriceLevel,
)


def test_orderbook_synchronizer_applies_contiguous_delta() -> None:
    synchronizer = OrderBookSynchronizer(_snapshot(nonce=100))

    result = synchronizer.apply(
        OrderBookDelta(
            instrument_id="instrument:binance:spot:BTC/USDT",
            market_key="binance_spot_btc_usdt",
            time=_time(),
            first_nonce=101,
            last_nonce=102,
            changes=(
                OrderBookChange("bid", Decimal("99"), Decimal("2")),
                OrderBookChange("ask", Decimal("101"), Decimal("0")),
            ),
        )
    )

    assert result.book.nonce == 102
    assert result.book.bids[0] == PriceLevel(Decimal("100"), Decimal("1"))
    assert PriceLevel(Decimal("99"), Decimal("2")) in result.book.bids
    assert result.book.asks == ()
    assert result.update_count == 1


def test_orderbook_synchronizer_ignores_stale_delta() -> None:
    synchronizer = OrderBookSynchronizer(_snapshot(nonce=100))

    result = synchronizer.apply(
        OrderBookDelta(
            instrument_id="instrument:binance:spot:BTC/USDT",
            market_key="binance_spot_btc_usdt",
            time=_time(),
            first_nonce=90,
            last_nonce=99,
            changes=(OrderBookChange("bid", Decimal("98"), Decimal("3")),),
        )
    )

    assert result.book.nonce == 100
    assert result.update_count == 0


def test_orderbook_synchronizer_marks_gap_stale() -> None:
    synchronizer = OrderBookSynchronizer(_snapshot(nonce=100))

    with pytest.raises(OrderBookSyncGap):
        synchronizer.apply(
            OrderBookDelta(
                instrument_id="instrument:binance:spot:BTC/USDT",
                market_key="binance_spot_btc_usdt",
                time=_time(),
                first_nonce=102,
                last_nonce=103,
                changes=(OrderBookChange("bid", Decimal("99"), Decimal("2")),),
            )
        )

    assert synchronizer.status == "stale"
    assert synchronizer.gap_count == 1


def _snapshot(*, nonce: int) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        instrument_id="instrument:binance:spot:BTC/USDT",
        market_key="binance_spot_btc_usdt",
        time=_time(),
        bids=(PriceLevel(Decimal("100"), Decimal("1")),),
        asks=(PriceLevel(Decimal("101"), Decimal("1")),),
        nonce=nonce,
        source="binance",
        derivation="local_l2",
    )


def _time() -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc)
