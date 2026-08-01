from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from kairospy.core.reference import MarketRef
from kairospy.infrastructure.integrations.payloads.ccxt_market import (
    ccxt_funding_rate_observation,
    ccxt_ohlcv_bar,
    ccxt_option_greeks_observation,
    ccxt_order_book_snapshot,
    ccxt_trade_print,
)
from kairospy.infrastructure.integrations.payloads.types import RawPayload
from kairospy.infrastructure.persistence.market_data.records import (
    BarRecord,
    OptionGreeksRecord,
    OrderBookRecord,
    QuoteRecord,
    RateRecord,
    TradeRecord,
    bar_record,
    funding_rate_record,
    option_greeks_record,
    order_book_record,
    ticker_record,
    trade_print_record,
)


def ccxt_ohlcv_record(values: list[Any] | tuple[Any, ...], *, market: MarketRef, timeframe: str) -> BarRecord:
    return bar_record(
        ccxt_ohlcv_bar(values, market=market, timeframe=timeframe),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_ticker_record(raw: RawPayload, *, market: MarketRef) -> QuoteRecord:
    return ticker_record(venue=market.venue, market=market.market, instrument=market, ticker=_normalized_ticker(raw))


def _normalized_ticker(raw: RawPayload) -> RawPayload:
    value = dict(raw)
    price = _first_not_none(value.get("last"), value.get("close"), value.get("markPrice"), value.get("indexPrice"), _info_price(value))
    if value.get("bid") is None and price is not None:
        value["bid"] = price
    if value.get("ask") is None and price is not None:
        value["ask"] = price
    if value.get("timestamp") is None:
        value["timestamp"] = int(datetime.now(timezone.utc).timestamp() * 1000)
    return value


def _info_price(raw: RawPayload) -> object | None:
    info = raw.get("info")
    return info.get("price") if isinstance(info, Mapping) else None


def _first_not_none(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


def ccxt_order_book_record(raw: RawPayload, *, market: MarketRef) -> OrderBookRecord:
    return order_book_record(
        ccxt_order_book_snapshot(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_trade_record(raw: RawPayload, *, market: MarketRef) -> TradeRecord:
    return trade_print_record(
        ccxt_trade_print(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_funding_rate_record(raw: RawPayload, *, market: MarketRef) -> RateRecord:
    return funding_rate_record(
        ccxt_funding_rate_observation(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


def ccxt_option_greeks_record(raw: RawPayload, *, market: MarketRef) -> OptionGreeksRecord:
    return option_greeks_record(
        ccxt_option_greeks_observation(raw, market=market),
        venue=market.venue,
        market=market.market,
        source_symbol=market.source_symbol,
    )


__all__ = [
    "ccxt_funding_rate_record",
    "ccxt_ohlcv_record",
    "ccxt_option_greeks_record",
    "ccxt_order_book_record",
    "ccxt_ticker_record",
    "ccxt_trade_record",
]
