from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal
import re
from typing import Any


def base_record(
    *,
    kind: str,
    venue: str,
    instrument: object,
    time: object | None,
    market: str = "spot",
    source_symbol: object | None = None,
) -> dict[str, object]:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument, source_symbol=source_symbol)
    return {
        "time": iso_time(time),
        "kind": kind,
        **market_ref.identity_fields(),
    }


def ohlcv_record(
    *,
    venue: str,
    instrument: object,
    timeframe: str,
    values: list[Any] | tuple[Any, ...],
    market: str = "spot",
) -> dict[str, object]:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument)
    return {
        "time": iso_time(values[0]),
        "kind": "ohlcv",
        **market_ref.identity_fields(),
        "timeframe": timeframe,
        "open": _decimal_text(values[1]),
        "high": _decimal_text(values[2]),
        "low": _decimal_text(values[3]),
        "close": _decimal_text(values[4]),
        "volume": _decimal_text(values[5]),
    }


def ticker_record(*, venue: str, instrument: object, ticker: Mapping[str, Any], market: str = "spot") -> dict[str, object]:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument)
    return {
        "time": iso_time(ticker.get("timestamp")),
        "kind": "ticker",
        **market_ref.identity_fields(),
        "bid1": _optional_decimal_text(ticker.get("bid")),
        "ask1": _optional_decimal_text(ticker.get("ask")),
        "last": _optional_decimal_text(ticker.get("last")),
        "base_volume": _optional_decimal_text(ticker.get("baseVolume")),
        "quote_volume": _optional_decimal_text(ticker.get("quoteVolume")),
    }


def orderbook_record(*, venue: str, instrument: object, book: Mapping[str, Any], market: str = "spot") -> dict[str, object]:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument)
    bids = [level for value in book.get("bids", ()) if (level := _level(value)) is not None]
    asks = [level for value in book.get("asks", ()) if (level := _level(value)) is not None]
    bid1 = bids[0][0] if bids else _optional_decimal_from_keys(book, "bid1", "bid", "bid_price")
    bid1_size = bids[0][1] if bids else _optional_decimal_from_keys(book, "bid1_size", "bid_size")
    ask1 = asks[0][0] if asks else _optional_decimal_from_keys(book, "ask1", "ask", "ask_price")
    ask1_size = asks[0][1] if asks else _optional_decimal_from_keys(book, "ask1_size", "ask_size")
    return {
        "time": iso_time(book.get("timestamp")),
        "kind": "orderbook",
        **market_ref.identity_fields(),
        "bid1": bid1,
        "bid1_size": bid1_size,
        "ask1": ask1,
        "ask1_size": ask1_size,
        "bid_depth": len(bids) if bids else int(book.get("bid_depth", 1 if bid1 is not None else 0)),
        "ask_depth": len(asks) if asks else int(book.get("ask_depth", 1 if ask1 is not None else 0)),
        "bids": [list(level) for level in bids],
        "asks": [list(level) for level in asks],
        "nonce": book.get("nonce"),
    }


def trade_record(*, venue: str, instrument: object, trade: Mapping[str, Any], market: str = "spot") -> dict[str, object]:
    market_ref = _market_ref(venue=venue, market=market, instrument=instrument)
    size = _optional_decimal_text(trade.get("amount"))
    return {
        "time": iso_time(trade.get("timestamp")),
        "kind": "trade",
        **market_ref.identity_fields(),
        "id": trade.get("id"),
        "side": trade.get("side"),
        "price": _optional_decimal_text(trade.get("price")),
        "size": size,
        "amount": size,
        "cost": _optional_decimal_text(trade.get("cost")),
    }


def iso_time(value: object | None) -> str:
    millis = int(value) if value is not None else int(datetime.now(timezone.utc).timestamp() * 1000)
    return datetime.fromtimestamp(millis / 1000, timezone.utc).isoformat()


def _market_ref(
    *,
    venue: str,
    market: str,
    instrument: object,
    source_symbol: object | None = None,
) -> _RecordMarketRef:
    if hasattr(instrument, "identity_fields"):
        return instrument
    return _RecordMarketRef.ephemeral(
        venue=venue,
        market=market,
        source_symbol=str(instrument if source_symbol is None else source_symbol),
    )


class _RecordMarketRef:
    def __init__(
        self,
        *,
        market_id: str,
        instrument_id: str,
        market_key: str,
        venue: str,
        market: str,
        source_symbol: str,
    ) -> None:
        self.market_id = market_id
        self.instrument_id = instrument_id
        self.market_key = market_key
        self.venue = venue
        self.market = market
        self.source_symbol = source_symbol

    @classmethod
    def ephemeral(cls, *, venue: str, market: str, source_symbol: str) -> "_RecordMarketRef":
        base, quote = _split_symbol(source_symbol)
        instrument_id = (
            f"instrument:{_slug(market)}:{_slug(base)}:{_slug(quote)}"
            if base and quote
            else f"instrument:{_slug(market)}:{_slug(venue)}:{_slug(source_symbol)}"
        )
        return cls(
            market_id=f"market:{_slug(venue)}:{_slug(market)}:{_slug(source_symbol)}",
            instrument_id=instrument_id,
            market_key=f"{_slug(venue)}_{_slug(market)}_{_slug(source_symbol)}",
            venue=venue,
            market=market,
            source_symbol=source_symbol,
        )

    def identity_fields(self) -> dict[str, object]:
        return {
            "market_id": self.market_id,
            "instrument_id": self.instrument_id,
            "market_key": self.market_key,
            "venue": self.venue,
            "market": self.market,
            "source_symbol": self.source_symbol,
        }


def _decimal_text(value: object) -> str:
    return str(Decimal(str(value)))


def _optional_decimal_text(value: object) -> str | None:
    return None if value is None else _decimal_text(value)


def _optional_decimal_from_keys(row: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        if row.get(key) is not None:
            return _decimal_text(row[key])
    return None


def _level(value: object) -> list[str] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    return [_decimal_text(value[0]), _decimal_text(value[1])]


def _split_symbol(symbol: str) -> tuple[str | None, str | None]:
    for separator in ("/", "-", "_"):
        if separator in symbol:
            left, right = symbol.split(separator, 1)
            return left.strip() or None, right.strip() or None
    return symbol.strip() or None, None


def _slug(value: object) -> str:
    text = str(value).strip().lower()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", text)).strip("_")
