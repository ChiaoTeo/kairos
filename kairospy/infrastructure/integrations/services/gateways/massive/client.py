from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.services.drivers.http import HttpDriver


class MassiveStocksRequestError(RuntimeError):
    """Raised when a Massive REST request fails."""


@dataclass(slots=True)
class MassiveStocksRestClient:
    """Private REST client used by the Massive market gateways."""

    api_key: str | None = None
    credential_id: str | None = None
    base_url: str = "http://api.massiveprivateserver.site"
    driver: HttpDriver = field(default_factory=HttpDriver)

    def __post_init__(self) -> None:
        self.api_key = self.api_key or credential_value(self.credential_id, "api_key")

    def aggregates(
        self,
        symbol: str,
        *,
        from_date: str,
        to_date: str,
        multiplier: int = 1,
        timespan: str = "day",
        adjusted: str = "true",
        sort: str = "asc",
        limit: int = 120,
    ) -> object:
        normalized = symbol.strip().upper()
        if not normalized:
            raise ValueError("Massive aggregate symbol is required")
        return self.get(
            f"/v2/aggs/ticker/{normalized}/range/{multiplier}/{timespan}/{from_date}/{to_date}",
            params={"adjusted": adjusted, "sort": sort, "limit": limit},
        )

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> object:
        if not self.api_key:
            raise MassiveStocksRequestError("Massive API key is required")
        query = {str(key): value for key, value in dict(params or {}).items() if value is not None}
        query["apiKey"] = self.api_key
        response = self.driver.request("GET", f"{self.base_url.rstrip('/')}/{path.lstrip('/')}", params=query, headers=None)
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text}
        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, Mapping) else None
            raise MassiveStocksRequestError(str(message or f"Massive request failed with HTTP {response.status_code}"))
        return payload


__all__ = ["MassiveStocksRequestError", "MassiveStocksRestClient"]
