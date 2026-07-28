from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from typing import Callable, Iterable, Mapping
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


HttpGet = Callable[[str, Mapping[str, str]], object]


@dataclass(frozen=True, slots=True)
class BinanceReferenceDriver:
    api_key: str | None = None
    base_url: str = "https://api.binance.com"
    http_get: HttpGet | None = None

    def fetch_delist_schedule(self, *, params: Mapping[str, object] | None = None) -> Iterable[Mapping[str, object]]:
        query = urlencode(dict(params or {}))
        suffix = f"?{query}" if query else ""
        url = f"{urljoin(self.base_url.rstrip('/') + '/', 'sapi/v1/spot/delist-schedule')}{suffix}"
        headers = {}
        key = self.api_key or os.getenv("BINANCE_API_KEY")
        if key:
            headers["X-MBX-APIKEY"] = key
        payload = (self.http_get or self._default_http_get)(url, headers)
        rows = payload if isinstance(payload, list) else []
        return tuple(_delist_schedule_row(row) for row in rows if isinstance(row, Mapping))

    @staticmethod
    def _default_http_get(url: str, headers: Mapping[str, str]) -> object:
        request = Request(url, headers={"Accept": "application/json", **dict(headers)})
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


def _delist_schedule_row(row: Mapping[str, object]) -> Mapping[str, object]:
    value = dict(row)
    delist_time = value.get("delistTime") or value.get("delist_time")
    if delist_time is None:
        raise ValueError(f"Binance delist schedule row is missing delistTime: {row!r}")
    symbols = value.get("symbols")
    if not isinstance(symbols, list):
        raise ValueError(f"Binance delist schedule row is missing symbols: {row!r}")
    at = datetime.fromtimestamp(int(delist_time) / 1000, tz=timezone.utc)
    return {
        "delist_time": at.isoformat(),
        "delist_time_ms": int(delist_time),
        "symbols": tuple(str(item) for item in symbols),
        "raw": value,
    }


__all__ = ["BinanceReferenceDriver"]
