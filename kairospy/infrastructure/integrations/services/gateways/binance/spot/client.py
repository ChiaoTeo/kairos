from __future__ import annotations

import hashlib
import hmac
import time
import json
import inspect
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from kairospy.infrastructure.integrations.services.drivers.http import HttpDriver
from kairospy.infrastructure.integrations.services.drivers.websocket import WebSocketDriver
from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.protocol import MillisecondClock
from kairospy.infrastructure.integrations.services.gateways.binance.spot.endpoints import (
    BinanceSpotEndpoint,
    BinanceSpotEndpointKind,
)


class BinanceRequestError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, status_code: int | None = None, payload: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.payload = payload


@dataclass(slots=True)
class BinanceSpotRestClient:
    """Private REST transport boundary; vendor request mapping stays above it."""

    credential_id: str | None = None
    endpoint: BinanceSpotEndpoint = field(
        default_factory=lambda: BinanceSpotEndpoint(
            BinanceSpotEndpointKind.PUBLIC_MARKET_REST,
            "https://api.binance.com",
        )
    )
    driver: HttpDriver = field(default_factory=HttpDriver)
    time_provider: MillisecondClock = field(default_factory=lambda: lambda: int(time.time() * 1000))
    api_key: str | None = field(init=False, default=None, repr=False)
    secret: str | None = field(init=False, default=None, repr=False)

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
        query = {str(key): value for key, value in dict(params or {}).items() if value is not None}
        headers: dict[str, str] = {}
        if self.credential_id is not None:
            if not self.api_key:
                raise BinanceRequestError("Binance request requires an API key")
            headers["X-MBX-APIKEY"] = self.api_key
        if signed:
            if not self.secret:
                raise BinanceRequestError("Binance signed request requires an API secret")
            query.setdefault("recvWindow", 5000)
            query["timestamp"] = self.time_provider()
            query["signature"] = hmac.new(
                self.secret.encode("utf-8"),
                urlencode(tuple((key, str(value)) for key, value in query.items()), doseq=True).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        response = self.driver.request(method, self._url(path), params=query, headers=headers)
        payload = _response_payload(response)
        if response.status_code >= 400:
            code, message = _error_payload(payload)
            raise BinanceRequestError(message or f"Binance request failed with HTTP {response.status_code}", code=code, status_code=response.status_code, payload=payload)
        return payload

    def get(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = False) -> object:
        return self.request("GET", path, params=params, signed=signed)

    def post(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = True) -> object:
        return self.request("POST", path, params=params, signed=signed)

    def delete(self, path: str, *, params: Mapping[str, Any] | None = None, signed: bool = True) -> object:
        return self.request("DELETE", path, params=params, signed=signed)

    def _url(self, path: str) -> str:
        return f"{self.endpoint.base_url.rstrip('/')}/{path.lstrip('/')}"

@dataclass(slots=True)
class BinanceSpotRequestClient(BinanceSpotRestClient):
    """WebSocket request/response transport boundary."""

    endpoint: BinanceSpotEndpoint = field(
        default_factory=lambda: BinanceSpotEndpoint(
            BinanceSpotEndpointKind.REQUEST_API,
            "wss://ws-api.binance.com:443/ws-api/v3",
        )
    )
    driver: WebSocketDriver = field(default_factory=WebSocketDriver)

    async def request_api(self, method: str, *, params: Mapping[str, Any] | None = None) -> object:
        session = await self.driver.connect(self.endpoint.base_url)
        request_id = int(self.time_provider())
        payload = {"id": request_id, "method": method, "params": dict(params or {})}
        if self.credential_id is not None:
            if not self.api_key or not self.secret:
                raise BinanceRequestError("Binance request API requires API key and secret")
            request_params = payload["params"]
            assert isinstance(request_params, dict)
            request_params.setdefault("apiKey", self.api_key)
            request_params.setdefault("timestamp", self.time_provider())
            request_params["signature"] = hmac.new(
                self.secret.encode("utf-8"),
                urlencode(tuple((key, str(value)) for key, value in request_params.items()), doseq=True).encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        await session.send(json.dumps(payload))
        try:
            response = await session.recv()
            value = json.loads(response) if isinstance(response, str) else response
            if not isinstance(value, Mapping):
                raise BinanceRequestError("Binance request API returned a non-object response", payload=value)
            if value.get("status", 200) >= 400 or "error" in value:
                error = value.get("error")
                if isinstance(error, Mapping):
                    raise BinanceRequestError(str(error.get("msg") or "Binance request API failed"), code=_optional_int(error.get("code")), payload=value)
                raise BinanceRequestError("Binance request API failed", payload=value)
            return value.get("result", value)
        finally:
            close = getattr(session, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result


def _response_payload(response: Any) -> object:
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {"msg": response.text}


def _error_payload(payload: object) -> tuple[int | None, str]:
    if not isinstance(payload, Mapping):
        return None, ""
    code = payload.get("code")
    try:
        parsed_code = None if code is None else int(code)
    except (TypeError, ValueError):
        parsed_code = None
    return parsed_code, str(payload.get("msg") or "")


def _optional_int(value: object) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


__all__ = ["BinanceRequestError", "BinanceSpotRequestClient", "BinanceSpotRestClient"]
