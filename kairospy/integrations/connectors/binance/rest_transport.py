"""Binance REST transport protocol, urllib implementation, and rate limiter."""

from __future__ import annotations

import json
from time import monotonic, sleep
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class BinanceTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any: ...


class UrllibBinanceTransport:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def request(self, method, path, params=None, headers=None):
        query = urlencode(params or {}, doseq=True)
        url = f"{self.base_url}{path}"
        data = None
        if method.upper() in {"GET", "DELETE"} and query:
            url = f"{url}?{query}"
        elif query:
            data = query.encode()
        request = Request(url, data=data, headers=headers or {}, method=method.upper())
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())


class RateLimiter:
    def __init__(self, calls: int, period_seconds: float) -> None:
        self.calls, self.period = calls, period_seconds
        self._timestamps = []

    def acquire(self) -> None:
        now = monotonic()
        self._timestamps = [value for value in self._timestamps if now - value < self.period]
        if len(self._timestamps) >= self.calls:
            delay = self.period - (now - self._timestamps[0])
            if delay > 0:
                sleep(delay)
        self._timestamps.append(monotonic())
