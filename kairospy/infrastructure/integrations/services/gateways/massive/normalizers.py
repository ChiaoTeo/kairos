from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.domain.market import MarketEvent, MarketSubject, Quote, TradePrint
from kairospy.domain.reference import MarketRef


@dataclass(slots=True)
class MassiveStockNormalizers:
    """Translate Massive stock events into the canonical market model."""

    def market_domain_event(self, payload: Mapping[str, object], *, market: MarketRef, channel: str) -> MarketEvent:
        event = str(payload.get("ev") or "").strip().upper()
        observed_at = _timestamp(payload.get("t"))
        if event == "Q" and channel == "ticker":
            value = Quote(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=observed_at,
                bid=_decimal(payload.get("bp")),
                ask=_decimal(payload.get("ap")),
                bid_size=_decimal(payload.get("bs")),
                ask_size=_decimal(payload.get("as")),
                source="massive",
                basis="nbbo",
            )
        elif event == "T" and channel == "trade":
            price = _decimal(payload.get("p"))
            size = _decimal(payload.get("s"))
            value = TradePrint(
                instrument_id=market.instrument_id,
                market_id=market.market_id,
                market_key=market.market_key,
                time=observed_at,
                trade_id=_text(payload.get("i")) or None,
                price=price,
                size=size,
                cost=None if price is None or size is None else price * size,
                source="massive",
            )
        else:
            raise ValueError(f"unsupported Massive stock event: {event!r} for channel {channel!r}")
        sequence = _positive_int(payload.get("q"))
        return MarketEvent(
            subject=MarketSubject("market", market.market_id),
            observed_at=observed_at,
            value=value,
            available_at=datetime.now(timezone.utc),
            source="massive",
            sequence=sequence,
            metadata={
                "symbol": _text(payload.get("sym")),
                "channel": channel,
                "exchange": payload.get("x"),
                "conditions": payload.get("c"),
            },
        )


def _timestamp(value: object) -> datetime:
    try:
        number = float(value)
        # Massive stock trade and quote timestamps are nanoseconds.
        if number > 10_000_000_000_000:
            number /= 1_000_000_000
        elif number > 10_000_000_000:
            number /= 1_000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(timezone.utc)


def _decimal(value: object) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except Exception:
        return None


def _positive_int(value: object) -> int | None:
    try:
        integer = int(value) if value is not None else 0
    except (TypeError, ValueError):
        return None
    return integer if integer > 0 else None


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


__all__ = ["MassiveStockNormalizers"]
