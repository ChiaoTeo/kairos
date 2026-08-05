from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from collections.abc import Iterable
from datetime import datetime, timezone
import logging
import os
import re
from decimal import Decimal
from typing import Any

from kairospy.domain.market import Bar
from kairospy.domain.reference import MarketRef

from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.services.drivers.http import HttpDriver


_LOGGER = logging.getLogger("kairospy.massive.rest")


def _massive_http_driver() -> HttpDriver:
    raw_timeout = os.getenv("MASSIVE_HTTP_TIMEOUT_SECONDS", "10")
    try:
        timeout_seconds = float(raw_timeout)
    except ValueError:
        timeout_seconds = 10.0
    return HttpDriver(timeout_seconds=timeout_seconds)


class MassiveStocksRequestError(RuntimeError):
    """Raised when a Massive REST request fails."""


@dataclass(slots=True)
class MassiveStocksRestClient:
    """Private REST client used by the Massive market gateways."""

    api_key: str | None = None
    credential_id: str | None = None
    base_url: str = "http://api.massiveprivateserver.site"
    driver: HttpDriver = field(default_factory=_massive_http_driver)

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
        normalized = str(symbol).strip().upper()
        if not normalized:
            raise ValueError("Massive aggregate symbol is required")
        return self.get(
            f"/v2/aggs/ticker/{normalized}/range/{multiplier}/{timespan}/{from_date}/{to_date}",
            params={"adjusted": adjusted, "sort": sort, "limit": limit},
        )

    def bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        limit: int = 1000,
        market: str = "option",
        adapter_options: Mapping[str, object] | None = None,
    ) -> Iterable[Bar]:
        """Return normalized aggregate bars for equities or option contracts.

        Massive uses the same aggregates route for an option ticker such as
        ``O:SPY260821P00500000``.  Keeping the translation here means the
        market application receives domain Bars and never sees the vendor
        response shape.
        """
        multiplier, timespan = _timeframe(timeframe)
        payload = self.aggregates(
            symbol,
            from_date=_date(since),
            to_date=_date(until),
            multiplier=multiplier,
            timespan=timespan,
            limit=limit,
        )
        results = payload.get("results", ()) if isinstance(payload, Mapping) else ()
        ref = MarketRef.ephemeral(venue="massive", market=market, source_symbol=symbol)
        for item in results if isinstance(results, Iterable) else ():
            if not isinstance(item, Mapping) or item.get("t") is None:
                continue
            observed = _timestamp(item["t"])
            yield Bar(
                instrument_id=ref.instrument_id,
                market_id=ref.market_id,
                market_key=ref.market_key,
                time=observed,
                timeframe=timeframe,
                open=_decimal(item.get("o")),
                high=_decimal(item.get("h")),
                low=_decimal(item.get("l")),
                close=_decimal(item.get("c")),
                volume=_decimal(item.get("v")),
                source="massive",
            )

    def option_contracts(
        self,
        underlying: str = "SPY",
        *,
        as_of: str | None = None,
        expired: bool = False,
        limit: int = 1000,
    ) -> Iterable[Mapping[str, object]]:
        """Fetch option contract definitions, leaving catalog translation to Reference."""
        payload = self.get(
            "/v3/reference/options/contracts",
            params={
                "underlying_ticker": str(underlying).strip().upper(),
                "as_of": as_of,
                "expired": str(expired).lower(),
                "limit": limit,
                "sort": "expiration_date",
                "order": "asc",
            },
        )
        results = payload.get("results", ()) if isinstance(payload, Mapping) else ()
        return tuple(item for item in results if isinstance(item, Mapping))

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> object:
        if not self.api_key:
            raise MassiveStocksRequestError("Massive API key is required")
        query = {str(key): value for key, value in dict(params or {}).items() if value is not None}
        query["apiKey"] = self.api_key
        target = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = self.driver.request("GET", target, params=query, headers=None)
        except Exception as error:
            reason = _redact_secret(f"{type(error).__name__}: {error}")
            _LOGGER.error(
                "massive_rest request=failed target=%s timeout_seconds=%s reason=%s",
                path,
                getattr(self.driver, "timeout_seconds", "-"),
                reason,
            )
            raise MassiveStocksRequestError(
                f"Massive request failed ({path}); "
                f"timeout_seconds={getattr(self.driver, 'timeout_seconds', '-')}; "
                f"reason={reason}"
            ) from None
        try:
            payload = response.json()
        except ValueError:
            payload = {"message": response.text}
        if response.status_code >= 400:
            message = payload.get("message") if isinstance(payload, Mapping) else None
            raise MassiveStocksRequestError(str(message or f"Massive request failed with HTTP {response.status_code}"))
        return payload


__all__ = ["MassiveStocksRequestError", "MassiveStocksRestClient"]


def _timeframe(value: str) -> tuple[int, str]:
    text = value.strip().lower()
    if len(text) < 2 or not text[:-1].isdigit() or int(text[:-1]) < 1:
        raise ValueError(f"invalid Massive timeframe: {value!r}")
    unit = {"m": "minute", "h": "hour", "d": "day", "w": "week"}.get(text[-1])
    if unit is None:
        raise ValueError(f"unsupported Massive timeframe: {value!r}")
    return int(text[:-1]), unit


def _date(value: datetime | str | None) -> str:
    if value is None:
        return datetime.now().date().isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _timestamp(value: object) -> datetime:
    number = float(value)
    if number > 10_000_000_000_000:
        number /= 1_000_000_000
    elif number > 10_000_000_000:
        number /= 1_000
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _decimal(value: object) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _redact_secret(value: str) -> str:
    return re.sub(r"(?i)(api[_-]?key)=([^&\s)]+)", r"\1=<redacted>", value)
