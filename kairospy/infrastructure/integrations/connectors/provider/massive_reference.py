from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timezone
from decimal import Decimal

from kairospy.core.reference import ReferenceCatalog
from kairospy.core.reference.model import LifecycleEvent, LifecycleEventType, MarketDefinition
from kairospy.infrastructure.integrations.types import RawPayload, RawPayloadRows


def massive_split_events(
    rows: RawPayloadRows,
    *,
    catalog: ReferenceCatalog,
    venue: str | None = None,
) -> tuple[LifecycleEvent, ...]:
    events: list[LifecycleEvent] = []
    for row in rows:
        ticker = _ticker(row)
        effective_at = _date(row.get("execution_date") or row.get("ex_date"))
        market = _resolve_equity_market(catalog, ticker, effective_at, venue=venue)
        split_from = Decimal(str(row["split_from"]))
        split_to = Decimal(str(row["split_to"]))
        ratio = split_to / split_from
        if ratio <= 0 or ratio == 1:
            raise ValueError(f"Massive split ratio must be positive and not one for {ticker}")
        events.append(
            LifecycleEvent(
                LifecycleEventType.SPLIT,
                effective_at,
                instrument_id=market.instrument_id,
                listing_id=market.listing_id,
                market_id=market.market_id,
                venue=market.venue,
                source_symbol=market.source_symbol,
                current={
                    "ratio": str(ratio),
                    "split_from": str(split_from),
                    "split_to": str(split_to),
                    **_metadata(row, "id", "adjustment_type", "historical_adjustment_factor"),
                },
            )
        )
    return tuple(events)


def massive_dividend_events(
    rows: RawPayloadRows,
    *,
    catalog: ReferenceCatalog,
    venue: str | None = None,
) -> tuple[LifecycleEvent, ...]:
    events: list[LifecycleEvent] = []
    for row in rows:
        ticker = _ticker(row)
        ex_date = _date(row.get("ex_dividend_date") or row.get("ex_date"))
        pay_date = _date(row.get("pay_date") or row.get("ex_dividend_date") or row.get("ex_date"))
        market = _resolve_equity_market(catalog, ticker, ex_date, venue=venue)
        amount = Decimal(str(row["cash_amount"]))
        if amount <= 0:
            raise ValueError(f"Massive dividend amount must be positive for {ticker}")
        events.append(
            LifecycleEvent(
                LifecycleEventType.DIVIDEND,
                ex_date,
                instrument_id=market.instrument_id,
                listing_id=market.listing_id,
                market_id=market.market_id,
                venue=market.venue,
                source_symbol=market.source_symbol,
                current={
                    "amount_per_share": str(amount),
                    "currency": str(row.get("currency") or "USD"),
                    "pay_date": pay_date.isoformat(),
                    **_metadata(
                        row,
                        "id",
                        "declaration_date",
                        "distribution_type",
                        "frequency",
                        "historical_adjustment_factor",
                        "record_date",
                        "split_adjusted_cash_amount",
                    ),
                },
            )
        )
    return tuple(events)


def massive_ticker_change_events(
    rows: RawPayloadRows,
    *,
    catalog: ReferenceCatalog,
    ticker: str,
    venue: str | None = None,
) -> tuple[LifecycleEvent, ...]:
    changes = sorted((_ticker_change(row) for row in rows if _is_ticker_change(row)), key=lambda item: item[0])
    events: list[LifecycleEvent] = []
    previous_symbol = ""
    for effective_at, old_symbol, new_symbol, row in changes:
        if old_symbol:
            previous_symbol = old_symbol
        elif not previous_symbol:
            previous_symbol = new_symbol
            continue
        if new_symbol == previous_symbol:
            continue
        market = _resolve_equity_market(catalog, previous_symbol, effective_at, venue=venue)
        events.append(
            LifecycleEvent(
                LifecycleEventType.SYMBOL_CHANGED,
                effective_at,
                instrument_id=market.instrument_id,
                listing_id=market.listing_id,
                market_id=market.market_id,
                venue=market.venue,
                source_symbol=new_symbol,
                previous={"symbol": previous_symbol},
                current={"symbol": new_symbol, **_metadata(row, "type", "id")},
            )
        )
        previous_symbol = new_symbol
    return tuple(events)


def massive_corporate_action_events(
    *,
    splits: RawPayloadRows = (),
    dividends: RawPayloadRows = (),
    ticker_events: RawPayloadRows = (),
    catalog: ReferenceCatalog,
    ticker: str,
    venue: str | None = None,
) -> tuple[LifecycleEvent, ...]:
    values = [
        *massive_split_events(splits, catalog=catalog, venue=venue),
        *massive_dividend_events(dividends, catalog=catalog, venue=venue),
        *massive_ticker_change_events(ticker_events, catalog=catalog, ticker=ticker, venue=venue),
    ]
    return tuple(sorted(values, key=lambda item: (item.event_time, item.event_type.value, str(item.source_symbol or ""))))


def _resolve_equity_market(catalog: ReferenceCatalog, ticker: str, at: datetime, *, venue: str | None) -> MarketDefinition:
    candidates = [
        item for item in catalog.list_markets(at=at, venue=venue, market="equity")
        if str(item.source_symbol).casefold() == ticker.casefold()
    ]
    if not candidates:
        candidates = [
            item for item in catalog.markets()
            if str(item.market) == "equity"
            and (venue is None or str(item.venue) == str(venue))
            and str(item.source_symbol).casefold() == ticker.casefold()
            and item.effective_to == at
        ]
    if not candidates:
        raise KeyError(f"unknown Massive equity market for ticker {ticker} at {at.isoformat()}")
    if len(candidates) > 1:
        raise KeyError(f"ambiguous Massive equity market for ticker {ticker} at {at.isoformat()}")
    return candidates[0]


def _ticker(row: RawPayload) -> str:
    text = str(row.get("ticker") or row.get("symbol") or "").strip().upper()
    if not text:
        raise ValueError("Massive corporate action row is missing ticker")
    return text


def _date(value: object) -> datetime:
    if value is None:
        raise ValueError("Massive corporate action row is missing date")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Massive corporate action datetime must be timezone-aware")
        return value.astimezone(timezone.utc)
    return datetime.combine(datetime.fromisoformat(str(value)).date(), time.min, tzinfo=timezone.utc)


def _is_ticker_change(row: RawPayload) -> bool:
    return str(row.get("type") or row.get("event_type") or "").lower() in {"ticker_change", "symbol_change"}


def _ticker_change(row: RawPayload) -> tuple[datetime, str, str, RawPayload]:
    payload = row.get("ticker_change")
    nested = payload if isinstance(payload, Mapping) else {}
    old_symbol = str(row.get("old_ticker") or nested.get("old_ticker") or "").strip().upper()
    symbol = str(row.get("new_ticker") or nested.get("ticker") or "").strip().upper()
    if not symbol:
        raise ValueError("Massive ticker change row is missing new ticker")
    return _date(row.get("date") or row.get("effective_date")), old_symbol, symbol, row


def _metadata(row: RawPayload, *keys: str) -> dict[str, object]:
    return {key: str(row[key]) for key in keys if row.get(key) is not None}


__all__ = [
    "massive_corporate_action_events",
    "massive_dividend_events",
    "massive_split_events",
    "massive_ticker_change_events",
]
