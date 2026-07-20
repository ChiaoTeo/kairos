"""Binance request signing and clock synchronization."""

from __future__ import annotations

import hashlib
import hmac
from time import time
from typing import Any
from urllib.parse import urlencode

from .rest_transport import BinanceTransport, RateLimiter


class BinanceSigner:
    def __init__(self, api_key: str, secret: str, clock_offset_ms: int = 0) -> None:
        self.api_key, self.secret, self.clock_offset_ms = api_key, secret.encode(), clock_offset_ms

    def signed(self, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        values = dict(params or {})
        values.setdefault("timestamp", int(time() * 1000) + self.clock_offset_ms)
        values.setdefault("recvWindow", 5000)
        query = urlencode(values, doseq=True)
        values["signature"] = hmac.new(self.secret, query.encode(), hashlib.sha256).hexdigest()
        return values, {"X-MBX-APIKEY": self.api_key}

    def synchronize(self, server_time_ms: int, local_time_ms: int | None = None) -> int:
        local = int(time() * 1000) if local_time_ms is None else local_time_ms
        self.clock_offset_ms = server_time_ms - local
        return self.clock_offset_ms


def synchronize_clock(
    transport: BinanceTransport,
    signer: BinanceSigner,
    limiter: RateLimiter,
    *,
    futures: bool = False,
    inverse: bool = False,
    local_time_ms: int | None = None,
) -> int:
    path = "/dapi/v1/time" if inverse else "/fapi/v1/time" if futures else "/api/v3/time"
    limiter.acquire()
    row = transport.request("GET", path)
    return signer.synchronize(int(row["serverTime"]), local_time_ms)
