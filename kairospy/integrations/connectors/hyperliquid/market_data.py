from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Iterable, Mapping
from urllib import request as urllib_request


HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"


@dataclass(frozen=True, slots=True)
class HyperliquidInfoClient:
    url: str = HYPERLIQUID_INFO_URL
    timeout_seconds: float = 30.0

    def info(self, payload: Mapping[str, object]) -> object:
        body = json.dumps(dict(payload), separators=(",", ":")).encode("utf-8")
        req = urllib_request.Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib_request.urlopen(req, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))

    def candle_snapshot(self, *, coin: str, interval: str, start: datetime, end: datetime) -> list[dict[str, object]]:
        value = self.info({
            "type": "candleSnapshot",
            "req": {
                "coin": coin,
                "interval": interval,
                "startTime": _millis(start),
                "endTime": _millis(end),
            },
        })
        return _list_of_dicts(value)

    def funding_history(self, *, coin: str, start: datetime, end: datetime) -> list[dict[str, object]]:
        value = self.info({
            "type": "fundingHistory",
            "coin": coin,
            "startTime": _millis(start),
            "endTime": _millis(end),
        })
        return _list_of_dicts(value)


def hyperliquid_ohlcv_rows(
    client: HyperliquidInfoClient,
    *,
    coins: Iterable[str],
    interval: str,
    start: datetime,
    end: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for coin in coins:
        normalized = _coin(coin)
        for item in client.candle_snapshot(coin=normalized, interval=interval, start=start, end=end):
            rows.append({
                "period_start": _iso(item.get("t") or item.get("time")),
                "period_end": _iso(item.get("T") or item.get("closeTime")),
                "instrument_id": f"crypto:hyperliquid:perpetual:{normalized}",
                "coin": normalized,
                "interval": str(item.get("i") or interval),
                "open": _float(item.get("o") or item.get("open")),
                "high": _float(item.get("h") or item.get("high")),
                "low": _float(item.get("l") or item.get("low")),
                "close": _float(item.get("c") or item.get("close")),
                "volume": _float(item.get("v") or item.get("volume")),
                "trade_count": item.get("n"),
                "source": "hyperliquid",
            })
    return rows


def hyperliquid_funding_rows(
    client: HyperliquidInfoClient,
    *,
    coins: Iterable[str],
    start: datetime,
    end: datetime,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for coin in coins:
        normalized = _coin(coin)
        for item in client.funding_history(coin=normalized, start=start, end=end):
            rows.append({
                "event_time": _iso(item.get("time") or item.get("timestamp")),
                "instrument_id": f"crypto:hyperliquid:perpetual:{normalized}",
                "coin": str(item.get("coin") or normalized),
                "funding_rate": _float(item.get("fundingRate") or item.get("funding")),
                "premium": _float(item.get("premium")),
                "source": "hyperliquid",
            })
    return rows


def _list_of_dicts(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise RuntimeError("Hyperliquid info endpoint returned a non-list payload")
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _millis(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("Hyperliquid historical requests require timezone-aware timestamps")
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def _iso(value: object) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        result = datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    else:
        text = str(value)
        result = datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc) if text.isdigit() else datetime.fromisoformat(text.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).isoformat()


def _float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _coin(value: str) -> str:
    coin = str(value).strip().upper()
    if not coin:
        raise ValueError("Hyperliquid coin cannot be empty")
    return coin
