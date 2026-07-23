from __future__ import annotations

from datetime import datetime


def hyperliquid_historical_rows(product, request_args) -> list[dict[str, object]]:
    from kairospy.integrations.connectors.hyperliquid import (
        HyperliquidInfoClient,
        hyperliquid_funding_rows,
        hyperliquid_ohlcv_rows,
    )

    start = _parse_datetime(request_args.start, "start")
    end = _parse_datetime(request_args.end, "end")
    instruments = tuple(str(item) for item in getattr(request_args, "instrument", ()) or ())
    if not instruments:
        raise ValueError("Hyperliquid historical products require --instrument <coin>")
    client = getattr(request_args, "client", None) or HyperliquidInfoClient()
    protocol_name = str(getattr(product, "protocol_name", ""))
    key = str(getattr(product, "key", ""))
    if "ohlcv" in protocol_name:
        interval = "1m" if key.endswith(".1m") else "1h"
        return hyperliquid_ohlcv_rows(client, coins=instruments, interval=interval, start=start, end=end)
    if "funding" in protocol_name:
        return hyperliquid_funding_rows(client, coins=instruments, start=start, end=end)
    raise ValueError(f"unsupported Hyperliquid historical product: {key}")


def _parse_datetime(value: object, label: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return result


__all__ = ["hyperliquid_historical_rows"]
