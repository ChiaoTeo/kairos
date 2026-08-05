from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator
from dataclasses import dataclass

from kairospy.application.usecases.market.application.operations import MarketDataOperationsService
from kairospy.application.usecases.market.application.data import MarketDataSpec
from kairospy.application.usecases.market.application.data import parse_market_dataset_id
from kairospy.application.usecases.market.application.resolver import MarketDataResolver
from kairospy.application.usecases.market.application.requests import MarketDataRow
from .resources import DriverName, ExchangeName, MarketCommandResources, MarketStreamClient, StorageFormat


class MarketStreamCommandService:
    """System commands for one explicitly requested live stream session."""

    def __init__(self, resources: MarketCommandResources) -> None:
        self._resources = resources

    def stream_events(
        self,
        *,
        dataset: str | None,
        exchange_name: ExchangeName,
        driver_name: DriverName,
        kind: str | None,
        symbol: str | None,
        limit: int | None,
        book_limit: int | None,
        trade_limit: int,
        poll_seconds: float,
    ) -> AsyncIterable[MarketDataRow]:
        request = _live_request(dataset, kind, symbol, exchange_name, None)
        client: MarketStreamClient = self._resources.stream_market_access(request.exchange_name, driver_name)
        params: dict[str, str | int | float | bool | None] = {"poll_seconds": poll_seconds}
        if limit is not None:
            params["max_events"] = limit
        if request.kind == "ticker":
            return client.watch_ticker(request.symbol, params=params)
        if request.kind == "orderbook":
            return client.watch_order_book(request.symbol, limit=book_limit, params=params)
        if request.kind == "trades":
            return client.watch_trades(request.symbol, limit=trade_limit, params=params)
        if request.kind == "option_greeks":
            return client.watch_option_greeks(request.symbol, params=params)
        raise ValueError(f"unsupported market data stream kind: {request.kind}")

    async def persist(
        self,
        *,
        dataset: str | None,
        kind: str | None,
        symbol: str | None,
        root: str | None,
        storage_format: StorageFormat | None,
        exchange_name: ExchangeName,
        driver_name: DriverName,
        market: str,
        limit: int | None,
        book_limit: int | None,
        trade_limit: int,
        poll_seconds: float,
    ) -> int:
        request = _live_request(dataset, kind, symbol, exchange_name, market)
        spec = MarketDataSpec(
            symbol=request.symbol,
            kind=request.dataset_kind,
            venue=request.exchange_name.value,
            market=request.market,
            dataset=request.dataset,
        )
        events = self.stream_events(
            dataset=request.dataset,
            exchange_name=request.exchange_name,
            driver_name=driver_name,
            kind=request.kind,
            symbol=request.symbol,
            limit=limit,
            book_limit=book_limit,
            trade_limit=trade_limit,
            poll_seconds=poll_seconds,
        )
        operations = MarketDataOperationsService(
            self._resources.data_store(root, storage_format),
            resolver=MarketDataResolver(default_venue=request.exchange_name.value, default_market=request.market),
        )
        return await operations.persist(spec, events, limit=limit)


@dataclass(frozen=True, slots=True)
class _LiveRequest:
    dataset: str | None
    dataset_kind: str
    kind: str
    symbol: str
    exchange_name: ExchangeName
    market: str


def _live_request(
    dataset: str | None,
    kind: str | None,
    symbol: str | None,
    exchange_name: ExchangeName,
    market: str | None,
) -> _LiveRequest:
    if dataset is not None:
        parsed = parse_market_dataset_id(dataset)
        live_kind = _live_kind(parsed.kind)
        if live_kind not in {"ticker", "orderbook", "trades", "option_greeks"}:
            raise ValueError(f"dataset does not map to a live market stream: {dataset}")
        return _LiveRequest(parsed.dataset_id, parsed.kind, live_kind, parsed.source_symbol, ExchangeName(parsed.venue), parsed.market)
    if kind is None:
        raise ValueError("--kind is required when dataset is not provided")
    if symbol is None or not symbol.strip():
        raise ValueError("--symbol is required when dataset is not provided")
    live_kind = _live_kind(kind)
    if live_kind not in {"ticker", "orderbook", "trades", "option_greeks"}:
        raise ValueError(f"unsupported live market data kind: {kind}")
    return _LiveRequest(None, live_kind, live_kind, symbol, exchange_name, _normalize_market(market or ("option" if live_kind == "option_greeks" else "spot")))


def _live_kind(kind: str) -> str:
    value = str(kind).strip().lower()
    if value in {"quote", "quotes", "ticker"}:
        return "ticker"
    if value in {"trade", "trades"}:
        return "trades"
    if value in {"option_greeks", "greeks", "option-greeks"}:
        return "option_greeks"
    return value


def _normalize_market(value: object) -> str:
    aliases = {"linear": "swap", "perpetual": "swap", "perp": "swap", "futures": "future", "options": "option"}
    text = str(value).strip().lower()
    return aliases.get(text, text)


__all__ = ["MarketStreamCommandService"]
