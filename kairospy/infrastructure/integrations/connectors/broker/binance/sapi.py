from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping
from urllib.parse import urlencode

import requests

from kairospy.infrastructure.integrations.credentials import credential_value
from kairospy.infrastructure.integrations.types import IntegrationParams, RawPayload


TimeProvider = Callable[[], int]


class BinanceSapiError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, status_code: int | None = None, payload: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.payload = payload


@dataclass(frozen=True, slots=True)
class BinanceSapiClient:
    api_key: str | None = None
    secret: str | None = None
    base_url: str = "https://api.binance.com"
    recv_window: int = 5000
    session: requests.Session = field(default_factory=requests.Session)
    time_provider: TimeProvider = field(default_factory=lambda: lambda: int(time.time() * 1000))

    @classmethod
    def from_credential(cls, credential: str | None) -> "BinanceSapiClient":
        return cls(
            api_key=credential_value(credential, "API_KEY", "BINANCE_API_KEY"),
            secret=credential_value(credential, "SECRET", "BINANCE_SECRET"),
        )

    def get(
        self,
        path: str,
        *,
        params: IntegrationParams | None = None,
        signed: bool = False,
        api_key: bool = True,
    ) -> object:
        return self.request("GET", path, params=params, signed=signed, api_key=api_key)

    def post(
        self,
        path: str,
        *,
        params: IntegrationParams | None = None,
        signed: bool = True,
        api_key: bool = True,
    ) -> object:
        return self.request("POST", path, params=params, signed=signed, api_key=api_key)

    def delete(
        self,
        path: str,
        *,
        params: IntegrationParams | None = None,
        signed: bool = True,
        api_key: bool = True,
    ) -> object:
        return self.request("DELETE", path, params=params, signed=signed, api_key=api_key)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: IntegrationParams | None = None,
        signed: bool = False,
        api_key: bool = True,
    ) -> object:
        query = {str(key): value for key, value in dict(params or {}).items() if value is not None}
        headers: dict[str, str] = {}
        if api_key:
            if not self.api_key:
                raise BinanceSapiError("Binance SAPI request requires an API key")
            headers["X-MBX-APIKEY"] = self.api_key
        if signed:
            if not self.secret:
                raise BinanceSapiError("Binance signed SAPI request requires an API secret")
            query.setdefault("recvWindow", self.recv_window)
            query["timestamp"] = self.time_provider()
            query["signature"] = self._signature(query)

        response = self.session.request(method.upper(), self._url(path), params=query, headers=headers, timeout=30)
        payload = _response_payload(response)
        if response.status_code >= 400:
            code, message = _error_payload(payload)
            raise BinanceSapiError(message or f"Binance SAPI request failed with HTTP {response.status_code}", code=code, status_code=response.status_code, payload=payload)
        return payload

    def _signature(self, params: RawPayload) -> str:
        query = urlencode(tuple((key, str(value)) for key, value in params.items()), doseq=True)
        return hmac.new(self.secret_bytes, query.encode("utf-8"), hashlib.sha256).hexdigest()

    @property
    def secret_bytes(self) -> bytes:
        if self.secret is None:
            raise BinanceSapiError("Binance signed SAPI request requires an API secret")
        return self.secret.encode("utf-8")

    def _url(self, path: str) -> str:
        suffix = path if path.startswith("/") else f"/{path}"
        return f"{self.base_url.rstrip('/')}{suffix}"


def _response_payload(response: requests.Response) -> object:
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


__all__ = ["BinanceSapiClient", "BinanceSapiError"]
