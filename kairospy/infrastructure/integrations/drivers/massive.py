from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from typing import AsyncIterator, Callable, Iterable, Mapping
from urllib.parse import urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


HttpGet = Callable[[str], Mapping[str, object]]


@dataclass(frozen=True, slots=True)
class MassiveDriver:
    api_key: str | None = None
    base_url: str = "https://api.massive.com"
    http_get: HttpGet | None = None

    def fetch_markets(
        self,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        options = dict(params or {})
        asset_class = str(options.pop("asset_class", "equity")).lower()
        if asset_class not in {"equity", "stocks", "stock"}:
            raise ValueError(f"MassiveDriver.fetch_markets only supports equities for now: {asset_class}")
        include_inactive = _bool(options.pop("include_inactive", True))
        active_values = (True, False) if include_inactive and "active" not in options else (_bool(options.pop("active", True)),)
        rows: list[Mapping[str, object]] = []
        for active in active_values:
            page_options = dict(options)
            request_params = {
                "market": "stocks",
                "type": page_options.pop("type", "CS"),
                "active": str(active).lower(),
                "limit": int(page_options.pop("limit", 1000)),
                "sort": page_options.pop("sort", "ticker"),
                "order": page_options.pop("order", "asc"),
                **page_options,
            }
            for row in self._pages("/v3/reference/tickers", request_params):
                rows.append(_normalize_equity_row(row, active=bool(active)))
        return tuple(rows)

    def fetch_splits(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        request_params = {
            "ticker": ticker.upper(),
            "execution_date.gte": _date_param(start),
            "execution_date.lt": _date_param(end),
            "limit": 5000,
            **dict(params or {}),
        }
        return tuple(self._pages("/stocks/v1/splits", request_params))

    def fetch_dividends(
        self,
        ticker: str,
        *,
        start: datetime,
        end: datetime,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        request_params = {
            "ticker": ticker.upper(),
            "ex_dividend_date.gte": _date_param(start),
            "ex_dividend_date.lt": _date_param(end),
            "limit": 5000,
            **dict(params or {}),
        }
        return tuple(self._pages("/stocks/v1/dividends", request_params))

    def fetch_ticker_events(
        self,
        ticker: str,
        *,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        request_params = {"types": "ticker_change", **dict(params or {})}
        url = self._url(f"/vX/reference/tickers/{ticker.upper()}/events", request_params)
        payload = dict((self.http_get or self._default_http_get)(url))
        results = payload.get("results") or {}
        if not isinstance(results, Mapping):
            raise RuntimeError("Massive ticker events results must be an object")
        events = results.get("events") or ()
        if not isinstance(events, list):
            raise RuntimeError("Massive ticker events results.events must be a list")
        return tuple(dict(row) for row in events if isinstance(row, Mapping))

    def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        params: Mapping[str, object] | None = None,
    ) -> Iterable[Mapping[str, object]]:
        raise NotImplementedError("Massive historical market data driver is not implemented yet")

    async def watch_trades(
        self,
        symbol: str,
        *,
        since: object | None = None,
        limit: int = 50,
        params: Mapping[str, object] | None = None,
    ) -> AsyncIterator[Mapping[str, object]]:
        raise NotImplementedError("Massive live market data driver is not implemented yet")
        yield {}


    def _pages(self, path: str, params: Mapping[str, object]) -> Iterable[Mapping[str, object]]:
        url = self._url(path, params)
        while url:
            payload = dict((self.http_get or self._default_http_get)(url))
            results = payload.get("results") or ()
            if not isinstance(results, list):
                raise RuntimeError("Massive response results must be a list")
            for row in results:
                if isinstance(row, Mapping):
                    yield dict(row)
            next_url = payload.get("next_url")
            url = self._with_api_key(str(next_url)) if next_url else ""

    def _url(self, path: str, params: Mapping[str, object]) -> str:
        return self._with_api_key(f"{urljoin(self.base_url.rstrip('/') + '/', path.lstrip('/'))}?{urlencode(params)}")

    def _with_api_key(self, url: str) -> str:
        if url.startswith("/"):
            url = urljoin(self.base_url.rstrip("/") + "/", url.lstrip("/"))
        key = self.api_key or os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY")
        if not key:
            return url
        parsed = urlparse(url)
        query = parsed.query
        if "apiKey=" in query:
            return url
        separator = "&" if query else ""
        return urlunparse(parsed._replace(query=f"{query}{separator}apiKey={key}"))

    @staticmethod
    def _default_http_get(url: str) -> Mapping[str, object]:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))


def _normalize_equity_row(row: Mapping[str, object], *, active: bool) -> Mapping[str, object]:
    value = dict(row)
    if value.get("ticker"):
        value["ticker"] = str(value["ticker"]).upper()
    value["venue"] = value.get("primary_exchange") or value.get("venue") or "UNKNOWN"
    value["currency"] = value.get("currency_name") or value.get("currency") or "USD"
    value["active"] = bool(value.get("active", active))
    value["provider"] = "massive"
    value["security_type"] = value.get("type") or value.get("security_type")
    if value.get("ticker") and not value.get("source_symbol"):
        value["source_symbol"] = value["ticker"]
    if value.get("composite_figi") and not value.get("venue_instrument_id"):
        value["venue_instrument_id"] = value["composite_figi"]
    return value


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _date_param(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Massive corporate action date range must be timezone-aware")
    return value.date().isoformat()


__all__ = ["MassiveDriver"]
