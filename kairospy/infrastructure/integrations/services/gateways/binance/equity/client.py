from __future__ import annotations

import time
import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from kairospy.infrastructure.integrations.protocol import MillisecondClock
from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.services.drivers.http import HttpDriver


class BinanceEquityRequestError(RuntimeError):
    pass


@dataclass(slots=True)
class BinanceEquityRestClient:
    """REST boundary for Binance Stocks Trading REST endpoints."""

    credential_id: str | None = None
    driver: HttpDriver = field(default_factory=HttpDriver)
    time_provider: MillisecondClock = field(default_factory=lambda: lambda: int(time.time() * 1000))
    api_key: str | None = field(init=False, default=None, repr=False)
    secret: str | None = field(init=False, default=None, repr=False)
    clock_offset_ms: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.api_key = credential_value(self.credential_id, "API_KEY")
        self.secret = credential_value(self.credential_id, "SECRET")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        signed: bool = False,
    ) -> object:
        if not self.api_key:
            raise BinanceEquityRequestError("Binance Stocks Trading REST requests require an API key")
        query = {str(key): value for key, value in dict(params or {}).items() if value is not None}
        if signed:
            if not self.secret:
                raise BinanceEquityRequestError("Binance Stocks Trading signed requests require an API secret")
            query.setdefault("recvWindow", 10000)
            self._sign(query)
        response = self.driver.request(
            method,
            f"https://api.binance.com/{path.lstrip('/')}",
            params=query,
            headers={"X-MBX-APIKEY": self.api_key},
        )
        payload = _response_payload(response)
        if signed and response.status_code >= 400 and _error_code(payload) == -1021:
            self._synchronize_clock()
            self._sign(query)
            response = self.driver.request(
                method,
                f"https://api.binance.com/{path.lstrip('/')}",
                params=query,
                headers={"X-MBX-APIKEY": self.api_key},
            )
            payload = _response_payload(response)
        if response.status_code >= 400:
            message = _error_message(payload)
            raise BinanceEquityRequestError(str(message or f"Binance request failed with HTTP {response.status_code}"))
        return payload

    def get(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False) -> object:
        return self.request("GET", path, params=params, signed=signed)

    def post(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = True) -> object:
        return self.request("POST", path, params=params, signed=signed)

    def delete(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = True) -> object:
        return self.request("DELETE", path, params=params, signed=signed)

    def _sign(self, query: dict[str, Any]) -> None:
        query["timestamp"] = self.time_provider() + self.clock_offset_ms
        query.pop("signature", None)
        assert self.secret is not None
        query["signature"] = hmac.new(
            self.secret.encode("utf-8"),
            urlencode(tuple((key, str(value)) for key, value in query.items()), doseq=True).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _synchronize_clock(self) -> None:
        payload = self.request("GET", "/api/v3/time")
        if not isinstance(payload, Mapping) or payload.get("serverTime") is None:
            raise BinanceEquityRequestError("Binance time endpoint returned no serverTime")
        self.clock_offset_ms = int(payload["serverTime"]) - self.time_provider()


def _response_payload(response: object) -> object:
    status_code = getattr(response, "status_code", 0)
    content = getattr(response, "content", b"")
    if not content:
        return None
    try:
        return response.json()  # type: ignore[no-any-return]
    except ValueError:
        return {"msg": getattr(response, "text", f"HTTP {status_code}")}


def _error_code(payload: object) -> int | None:
    if not isinstance(payload, Mapping):
        return None
    try:
        return int(payload.get("code"))
    except (TypeError, ValueError):
        return None


def _error_message(payload: object) -> object:
    if not isinstance(payload, Mapping):
        return None
    return payload.get("msg") or payload.get("message")


__all__ = ["BinanceEquityRequestError", "BinanceEquityRestClient"]
