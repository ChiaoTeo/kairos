from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Mapping

from trading.domain.identity import InstrumentId
from trading.market_data.events import MarketEventEnvelope, MarketEventType
from trading.reference import ProviderId, ReferenceCatalog


def decode_quotes(
    rows: Iterable[Mapping[str, object]], mappings: ReferenceCatalog, *,
    ingested_at: datetime, source_order_start: int = 0, ticker: str | None = None,
) -> tuple[MarketEventEnvelope, ...]:
    events = []
    for offset, row in enumerate(rows):
        row_ticker = _ticker(row, ticker)
        event_time = _timestamp(row, "participant_timestamp", "sip_timestamp", "timestamp")
        available_time = _timestamp(row, "sip_timestamp", "participant_timestamp", "timestamp")
        instrument_id = _resolve(mappings, "options", row_ticker, event_time)
        events.append(MarketEventEnvelope(
            instrument_id, event_time, max(event_time, available_time), ingested_at,
            "massive", "options.quotes", row_ticker, MarketEventType.QUOTE, source_order_start + offset,
            {
                "bid": _decimal(row.get("bid_price")), "ask": _decimal(row.get("ask_price")),
                "bid_size": _decimal(row.get("bid_size")), "ask_size": _decimal(row.get("ask_size")),
                "bid_exchange": row.get("bid_exchange"), "ask_exchange": row.get("ask_exchange"),
                "conditions": tuple(row.get("conditions", ())),
                "sequence_number": row.get("sequence_number"),
            },
            receive_time=available_time,
        ))
    return tuple(events)


def decode_trades(
    rows: Iterable[Mapping[str, object]], mappings: ReferenceCatalog, *,
    ingested_at: datetime, source_order_start: int = 0, ticker: str | None = None,
) -> tuple[MarketEventEnvelope, ...]:
    events = []
    for offset, row in enumerate(rows):
        row_ticker = _ticker(row, ticker)
        event_time = _timestamp(row, "participant_timestamp", "sip_timestamp", "timestamp")
        available_time = _timestamp(row, "sip_timestamp", "participant_timestamp", "timestamp")
        instrument_id = _resolve(mappings, "options", row_ticker, event_time)
        correction = int(row.get("correction") or 0)
        flags = ("correction",) if correction else ()
        events.append(MarketEventEnvelope(
            instrument_id, event_time, max(event_time, available_time), ingested_at,
            "massive", "options.trades", row_ticker, MarketEventType.TRADE, source_order_start + offset,
            {
                "price": _decimal(row.get("price")), "size": _decimal(row.get("size")),
                "exchange": row.get("exchange"), "conditions": tuple(row.get("conditions", ())),
                "trade_id": row.get("id"), "sequence_number": row.get("sequence_number"), "correction": correction,
            },
            receive_time=available_time,
            flags=flags,
        ))
    return tuple(events)


def decode_option_snapshots(
    rows: Iterable[Mapping[str, object]], mappings: ReferenceCatalog, *, ingested_at: datetime,
    source_order_start: int = 0,
) -> tuple[MarketEventEnvelope, ...]:
    events = []
    for offset, row in enumerate(rows):
        details = row.get("details") if isinstance(row.get("details"), Mapping) else {}
        ticker = str(details.get("ticker") or row.get("ticker") or "")
        if not ticker:
            raise ValueError("Massive option snapshot is missing contract ticker")
        timestamps = _snapshot_timestamps(row)
        if not timestamps:
            raise ValueError(f"Massive option snapshot has no provider timestamp: {ticker}")
        available_time = max(timestamps)
        instrument_id = _resolve(mappings, "options", ticker, available_time)
        quote = row.get("last_quote") if isinstance(row.get("last_quote"), Mapping) else {}
        trade = row.get("last_trade") if isinstance(row.get("last_trade"), Mapping) else {}
        greeks = row.get("greeks") if isinstance(row.get("greeks"), Mapping) else None
        events.append(MarketEventEnvelope(
            instrument_id, available_time, available_time, ingested_at, "massive", "options.snapshot", ticker,
            MarketEventType.OPTION_SNAPSHOT, source_order_start + offset,
            {"bid": _decimal(quote.get("bid")), "ask": _decimal(quote.get("ask")),
             "bid_size": _decimal(quote.get("bid_size")), "ask_size": _decimal(quote.get("ask_size")),
             "last_trade_price": _decimal(trade.get("price")), "last_trade_size": _decimal(trade.get("size")),
             "vendor_greeks": dict(greeks) if greeks is not None else None,
             "vendor_implied_volatility": _decimal(row.get("implied_volatility")),
             "vendor_open_interest": _decimal(row.get("open_interest")),
             "vendor_fmv": _decimal(row.get("fmv")), "details": dict(details)},
            flags=("missing_two_sided_quote",) if quote.get("bid") is None or quote.get("ask") is None else (),
        ))
    return tuple(events)


def decode_bars(
    rows: Iterable[Mapping[str, object]], mappings: ReferenceCatalog, *,
    ticker: str, source_namespace: str, ingested_at: datetime, interval_seconds: int,
) -> tuple[MarketEventEnvelope, ...]:
    events = []
    for offset, row in enumerate(rows):
        period_start = _millis(row.get("t") or row.get("timestamp"))
        period_end = period_start.fromtimestamp(period_start.timestamp() + interval_seconds, tz=timezone.utc)
        instrument_id = _resolve(mappings, source_namespace, ticker, period_start)
        events.append(MarketEventEnvelope(
            instrument_id, period_end, period_end, ingested_at, "massive", f"{source_namespace}.aggregates",
            ticker, MarketEventType.BAR, offset,
            {"period_start": period_start, "period_end": period_end, "open": _decimal(row.get("o")),
             "high": _decimal(row.get("h")), "low": _decimal(row.get("l")), "close": _decimal(row.get("c")),
             "volume": _decimal(row.get("v")), "vwap": _decimal(row.get("vw")), "transactions": row.get("n")},
        ))
    return tuple(events)


def _ticker(row: Mapping[str, object], fallback: str | None = None) -> str:
    value = row.get("ticker") or row.get("T") or fallback
    if not value:
        raise ValueError("Massive row is missing ticker")
    return str(value)


def _resolve(catalog: ReferenceCatalog, namespace: str, external_id: str, at: datetime) -> InstrumentId:
    mapping = catalog.resolve_provider_symbol(ProviderId("massive"), namespace, external_id, at)
    return InstrumentId(mapping.target_id)


def _timestamp(row: Mapping[str, object], *keys: str) -> datetime:
    for key in keys:
        if row.get(key) is not None:
            return _nanos(row[key])
    raise ValueError(f"Massive row is missing timestamp fields: {keys}")


def _nanos(value: object) -> datetime:
    number = int(value)
    seconds, nanos = divmod(number, 1_000_000_000)
    return datetime.fromtimestamp(seconds + nanos / 1_000_000_000, tz=timezone.utc)


def _millis(value: object) -> datetime:
    if value is None:
        raise ValueError("Massive aggregate is missing timestamp")
    return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _snapshot_timestamps(row: Mapping[str, object]) -> list[datetime]:
    values = []
    for container_name, field in (("last_quote", "last_updated"), ("last_trade", "sip_timestamp"),
                                  ("day", "last_updated"), ("underlying_asset", "last_updated")):
        container = row.get(container_name)
        if isinstance(container, Mapping) and container.get(field) is not None:
            values.append(_nanos(container[field]))
    if row.get("fmv_last_updated") is not None:
        values.append(_nanos(row["fmv_last_updated"]))
    return values
