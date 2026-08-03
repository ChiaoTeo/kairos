from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kairospy.infrastructure.integrations.protocol import MillisecondClock
from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.services.drivers.http import HttpDriver


class BinanceEquityRequestError(RuntimeError):
    pass


@dataclass(slots=True)
class BinanceEquityRestClient:
    """REST boundary for Binance Stocks Trading market-data endpoints."""

    credential_id: str | None = None
    driver: HttpDriver = field(default_factory=HttpDriver)
    time_provider: MillisecondClock = field(default_factory=lambda: lambda: int(time.time() * 1000))
    api_key: str | None = field(init=False, default=None, repr=False)

    def __post_init__(self) -> None:
        self.api_key = credential_value(self.credential_id, "API_KEY")

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> object:
        if not self.api_key:
            raise BinanceEquityRequestError("Binance Stocks Trading market data requires an API key")
        response = self.driver.request(
            "GET",
            f"https://api.binance.com/{path.lstrip('/')}",
            params={key: value for key, value in dict(params or {}).items() if value is not None},
            headers={"X-MBX-APIKEY": self.api_key},
        )
        try:
            payload = response.json()
        except ValueError:
            payload = {"msg": response.text}
        if response.status_code >= 400:
            message = payload.get("msg") if isinstance(payload, Mapping) else None
            raise BinanceEquityRequestError(str(message or f"Binance request failed with HTTP {response.status_code}"))
        if not response.content:
            return None
        return payload


__all__ = ["BinanceEquityRequestError", "BinanceEquityRestClient"]
