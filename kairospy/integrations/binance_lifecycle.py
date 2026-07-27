from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

from kairospy.reference import LifecycleEvent, LifecycleEventType, ReferenceCatalog


def delist_schedule_events(
    rows: Iterable[Mapping[str, object]],
    *,
    catalog: ReferenceCatalog | None = None,
    venue: str = "binance",
    market: str = "spot",
) -> tuple[LifecycleEvent, ...]:
    events: list[LifecycleEvent] = []
    for row in rows:
        event_time = _time(row["delist_time"])
        for symbol in row.get("symbols", ()):
            text = str(symbol)
            resolved = None
            if catalog is not None:
                try:
                    resolved = catalog.resolve_market(text, venue=venue, market=market, at=event_time)
                except KeyError:
                    resolved = None
            events.append(
                LifecycleEvent(
                    LifecycleEventType.DELISTED,
                    event_time,
                    instrument_id=None if resolved is None else resolved.instrument_id,
                    listing_id=None if resolved is None else resolved.listing_id,
                    market_id=None if resolved is None else resolved.market_id,
                    venue=venue,
                    source_symbol=text,
                    current={"scheduled": True, "market": market},
                )
            )
    return tuple(events)


def _time(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


__all__ = ["delist_schedule_events"]
