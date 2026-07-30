from __future__ import annotations

from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Literal

from kairospy.application.runtime.dispatch.context import RuntimeContext
from kairospy.application.ports import DataSubscription, MarketDataSubscriptionSpec
from kairospy.application.service.domain.market import MarketDataOperationsService, MarketDataResolver, MarketDataSpec, parse_market_dataset_id, replay_rows
from kairospy.application.service.modes.backtest import BacktestConfigurationError, configured_backtest
from kairospy.application.system.facade.resources import DriverName, ExchangeName, StorageFormat, data_store, exchange
from kairospy.application.system.facade.context import workspace as resolve_workspace
from kairospy.core.market import Bar

MarketDataMode = Literal["historical", "live"]


class MarketDataFacade:
    def capabilities(
        self,
        *,
        exchange_name: ExchangeName | None = None,
        market: str | None = None,
        driver_name: DriverName | None = None,
    ) -> dict[str, object]:
        driver = driver_name or DriverName.ccxt
        venues = (exchange_name,) if exchange_name is not None else tuple(ExchangeName)
        namespaces = [
            _market_capability(venue.value, namespace, driver)
            for venue in venues
            for namespace in _candidate_markets(venue, market)
        ]
        return {
            "driver": driver.value,
            "markets": namespaces,
            "count": len(namespaces),
        }

    def check(
        self,
        *,
        symbol: str,
        exchange_name: ExchangeName,
        market: str,
        kind: str,
        data_mode: MarketDataMode,
        timeframe: str | None = None,
        driver_name: DriverName = DriverName.ccxt,
    ) -> dict[str, object]:
        capability = _market_capability(exchange_name.value, market, driver_name)
        spec = MarketDataSpec(
            symbol=symbol,
            kind=_historical_kind(kind) if data_mode == "historical" else _live_kind(kind),
            venue=exchange_name.value,
            market=market,
            timeframe=timeframe,
        )
        available = _capability_supports(capability, kind=spec.kind, data_mode=data_mode)
        if data_mode == "historical" and spec.kind == "ohlcv" and spec.timeframe is None:
            available = False
            reason = "historical bar data requires --timeframe"
        else:
            reason = None if available else str(capability.get("reason") or f"{data_mode} {spec.kind} is not supported")
        resolved = self._service(None, None, exchange_name=exchange_name, market=market).resolve(spec)
        return {
            "valid": available,
            "reason": reason,
            "driver": driver_name.value,
            "venue": exchange_name.value,
            "market": market,
            "symbol": symbol,
            "kind": spec.kind,
            "mode": data_mode,
            "timeframe": timeframe,
            "dataset": resolved.dataset_id if data_mode == "historical" else None,
            "capability": capability,
        }

    def download(
        self,
        *,
        symbol: str,
        dataset: str | None,
        root: str | None,
        storage_format: StorageFormat | None,
        exchange_name: ExchangeName,
        driver_name: DriverName,
        market: str,
        kind: str,
        timeframe: str,
        start: str | None,
        end: str | None,
        limit: int,
        mode: str,
    ) -> str:
        client = exchange(exchange_name, driver_name)
        spec = MarketDataSpec(
            symbol=symbol,
            kind=kind,
            venue=exchange_name.value,
            market=market,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            dataset=dataset,
        )
        path = self._service(root, storage_format, exchange_name=exchange_name, market=market).download(spec, client, mode=mode)
        return str(path)

    def prefetch_backtest(
        self,
        *,
        config_path: str | Path,
        driver_name: DriverName,
        limit: int,
        mode: str,
        dry_run: bool = False,
    ) -> dict[str, object]:
        try:
            configured = configured_backtest(Path(config_path))
        except BacktestConfigurationError as error:
            raise ValueError(str(error)) from error
        collector = _SubscriptionCollector()
        context = RuntimeContext(
            strategy_id=configured.strategy.strategy_id,
            data=collector,
        )
        configured.strategy.on_start(context)
        subscriptions = collector.subscriptions()
        if not subscriptions:
            raise ValueError("strategy did not subscribe to market data")
        downloads: list[dict[str, object]] = []
        for subscription in subscriptions:
            for spec in _historical_specs(
                subscription.spec,
                start=configured.market_policy.start,
                end=configured.market_policy.end,
                limit=limit,
            ):
                exchange_name = _exchange_name(spec.venue)
                resolved = configured.data.resolve(spec)
                capability = _market_capability(exchange_name.value, str(spec.market), driver_name)
                supported = _capability_supports(capability, kind=spec.kind, data_mode="historical")
                if not supported:
                    raise ValueError(str(capability.get("reason") or f"historical {spec.kind} is not supported for {spec.venue} {spec.market}"))
                path = None if dry_run else configured.data.download(spec, exchange(exchange_name, driver_name), mode=mode)
                downloads.append(
                    {
                        "subscription": subscription.key,
                        "dataset": resolved.dataset_id,
                        "path": None if path is None else str(path),
                        "kind": spec.kind,
                        "symbol": spec.symbol,
                        "venue": spec.venue,
                        "market": spec.market,
                        "timeframe": spec.timeframe,
                        "start": spec.start,
                        "end": spec.end,
                        "supported": supported,
                        "status": capability["status"],
                    }
                )
        return {
            "launch_id": configured.launch_id,
            "config": str(Path(config_path)),
            "dry_run": dry_run,
            "count": len(downloads),
            "plan": downloads,
            "downloads": () if dry_run else downloads,
        }

    def list_datasets(self, *, root: str | None, storage_format: StorageFormat | None) -> dict[str, object]:
        store = data_store(root, storage_format)
        datasets = [str(item) for item in store.list()]
        return {"root": str(store.root), "datasets": datasets, "aliases": store.aliases(), "count": len(datasets)}

    def inspect_dataset(
        self,
        *,
        dataset: str,
        root: str | None,
        storage_format: StorageFormat | None,
        sample: int,
    ) -> dict[str, object]:
        store = data_store(root, storage_format)
        rows = store.read_rows(dataset)
        times = [str(row.get("time")) for row in rows if row.get("time") is not None]
        data_path = store._existing_data_path(dataset)
        return {
            "dataset": str(store.resolve(dataset)),
            "path": str(data_path) if data_path is not None else None,
            "rows": len(rows),
            "start": min(times) if times else None,
            "end": max(times) if times else None,
            "columns": sorted({key for row in rows for key in row}),
            "sample": rows[:sample],
        }

    def alias_dataset(
        self,
        *,
        dataset: str,
        alias: str,
        root: str | None,
        storage_format: StorageFormat | None,
    ) -> dict[str, object]:
        store = data_store(root, storage_format)
        path = store.alias(dataset, alias)
        workspace = resolve_workspace()
        workspace.operations.append("market.alias", target={"dataset": str(store.resolve(dataset)), "alias": alias}, payload={"path": path})
        return {"dataset": str(store.resolve(dataset)), "alias": alias, "path": str(path)}

    def prune(
        self,
        *,
        dataset: str,
        start: str,
        end: str,
        root: str | None,
        storage_format: StorageFormat | None,
    ) -> dict[str, object]:
        store = data_store(root, storage_format)
        result = store.delete_window(dataset, start=start, end=end)
        resolve_workspace().operations.append("market.prune", target={"dataset": str(result["dataset"])}, payload=result)
        return result

    def read(
        self,
        *,
        dataset: str | None,
        root: str | None,
        storage_format: StorageFormat | None,
        symbol: str | None,
        exchange_name: ExchangeName,
        market: str,
        kind: str,
        timeframe: str | None,
        start: str | None,
        end: str | None,
        columns: list[str] | None,
        limit: int | None,
    ) -> list[dict[str, object]]:
        if dataset is not None:
            return data_store(root, storage_format).read_rows(
                dataset,
                start=start,
                end=end,
                columns=columns,
                limit=limit,
            )
        if symbol is None:
            raise ValueError("dataset or --symbol is required")
        return self._service(root, storage_format, exchange_name=exchange_name, market=market).read(
            MarketDataSpec(
                symbol=symbol,
                kind=kind,
                venue=exchange_name.value,
                market=market,
                timeframe=timeframe,
                start=start,
                end=end,
                limit=limit,
            ),
            columns=columns,
        )

    def replay(
        self,
        *,
        dataset: str | None,
        root: str | None,
        storage_format: StorageFormat | None,
        symbol: str | None,
        exchange_name: ExchangeName,
        market: str,
        kind: str,
        timeframe: str | None,
        start: str | None,
        end: str | None,
        limit: int | None,
        speed: float,
        write: Callable[[list[dict[str, object]]], object],
    ) -> None:
        rows = self.read(
            dataset=dataset,
            root=root,
            storage_format=storage_format,
            symbol=symbol,
            exchange_name=exchange_name,
            market=market,
            kind=kind,
            timeframe=timeframe,
            start=start,
            end=end,
            columns=None,
            limit=limit,
        )
        replay_rows(rows, speed=speed, write=write)

    def stream_events(
        self,
        *,
        dataset: str | None = None,
        exchange_name: ExchangeName,
        driver_name: DriverName,
        kind: str | None,
        symbol: str | None,
        limit: int | None,
        book_limit: int | None,
        trade_limit: int,
        poll_seconds: float,
    ) -> AsyncIterable[Mapping[str, object]]:
        request = _live_market_request(
            dataset=dataset,
            kind=kind,
            symbol=symbol,
            exchange_name=exchange_name,
            market=None,
        )
        client = exchange(request.exchange_name, driver_name)
        params: dict[str, object] = {"poll_seconds": poll_seconds}
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
        request = _live_market_request(
            dataset=dataset,
            kind=kind,
            symbol=symbol,
            exchange_name=exchange_name,
            market=market,
        )
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
        return await self._service(root, storage_format, exchange_name=request.exchange_name, market=request.market).persist(spec, events, limit=limit)

    def doctor(self, *, exchange_name: ExchangeName, driver_name: DriverName) -> dict[str, object]:
        exchange(exchange_name, driver_name)
        return {"valid": True, "exchange": exchange_name.value, "driver": driver_name.value}

    def _service(
        self,
        root: str | None,
        storage_format: StorageFormat | None,
        *,
        exchange_name: ExchangeName,
        market: str,
    ) -> MarketDataOperationsService:
        return MarketDataOperationsService(
            data_store(root, storage_format),
            resolver=MarketDataResolver(default_venue=exchange_name.value, default_market=market),
        )


class _SubscriptionCollector:
    def __init__(self) -> None:
        self._subscriptions: dict[str, DataSubscription] = {}

    async def events(self):
        if False:
            yield

    def subscribe(self, spec: MarketDataSubscriptionSpec) -> DataSubscription:
        subscription = DataSubscription(spec.key, spec)
        self._subscriptions[subscription.key] = subscription
        return subscription

    def unsubscribe(self, subscription: DataSubscription | str) -> None:
        key = subscription if isinstance(subscription, str) else subscription.key
        self._subscriptions.pop(key, None)

    def subscriptions(self) -> tuple[DataSubscription, ...]:
        return tuple(self._subscriptions[key] for key in sorted(self._subscriptions))


def _historical_specs(
    subscription: MarketDataSubscriptionSpec,
    *,
    start: object,
    end: object,
    limit: int,
) -> tuple[MarketDataSpec, ...]:
    if subscription.dataset_id is not None:
        dataset = parse_market_dataset_id(subscription.dataset_id)
        if dataset.kind != "ohlcv":
            raise ValueError(f"historical prefetch only supports ohlcv dataset subscriptions, got {dataset.kind}")
        return (
            MarketDataSpec(
                symbol=dataset.source_symbol,
                kind=dataset.kind,
                venue=dataset.venue,
                market=dataset.market,
                timeframe=dataset.timeframe,
                start=start,
                end=end,
                limit=limit,
                dataset=dataset.dataset_id,
            ),
        )
    specs: list[MarketDataSpec] = []
    for selector in subscription.selectors:
        if selector.model is not Bar:
            raise ValueError(f"historical prefetch only supports Bar selectors, got {selector.model.__name__}")
        if selector.interval is None:
            raise ValueError("historical bar prefetch requires Bar.select(interval=...)")
        specs.append(
            MarketDataSpec(
                symbol=str(subscription.market.source_symbol),
                kind="ohlcv",
                venue=str(subscription.market.venue),
                market=str(subscription.market.market),
                timeframe=selector.interval,
                start=start,
                end=end,
                limit=limit,
            )
        )
    return tuple(specs)


def _exchange_name(value: object) -> ExchangeName:
    text = str(value)
    try:
        return ExchangeName(text)
    except ValueError as error:
        raise ValueError(f"unsupported market data exchange: {text}") from error


def _exchange_supported(exchange_name: ExchangeName, driver_name: DriverName) -> bool:
    if driver_name is not DriverName.ccxt:
        return False
    return exchange_name in {ExchangeName.binance, ExchangeName.hyperliquid, ExchangeName.okex, ExchangeName.okx}


def _candidate_markets(exchange_name: ExchangeName, market: str | None) -> tuple[str, ...]:
    if market is not None:
        return (_normalize_market(market),)
    if exchange_name is ExchangeName.binance:
        return ("spot", "future", "swap", "option")
    if exchange_name is ExchangeName.hyperliquid:
        return ("swap", "spot", "option")
    if exchange_name in {ExchangeName.okx, ExchangeName.okex}:
        return ("spot", "swap", "future", "option")
    return ()


def _market_capability(venue: str, market: str, driver_name: DriverName) -> dict[str, object]:
    venue_name = _normalize_venue(venue)
    market_name = _normalize_market(market)
    configured = driver_name is DriverName.ccxt and market_name in _configured_markets(venue_name)
    status = "configured" if configured else "not_configured"
    reason = None if configured else f"{venue_name} {market_name} market data is not configured"
    historical = _historical_capabilities(market_name) if configured else ()
    live = _live_capabilities(market_name) if configured else ()
    return {
        "venue": venue_name,
        "exchange": venue_name,
        "market": market_name,
        "driver": driver_name.value,
        "status": status,
        "reason": reason,
        "historical": historical,
        "live": live,
    }


def _configured_markets(venue: str) -> set[str]:
    if venue == "binance":
        return {"spot", "future", "swap", "option"}
    if venue == "hyperliquid":
        return {"swap"}
    if venue in {"okx", "okex"}:
        return {"spot", "swap"}
    return set()


@dataclass(frozen=True, slots=True)
class _LiveMarketRequest:
    dataset: str | None
    dataset_kind: str
    kind: str
    symbol: str
    exchange_name: ExchangeName
    market: str


def _live_market_request(
    *,
    dataset: str | None,
    kind: str | None,
    symbol: str | None,
    exchange_name: ExchangeName,
    market: str | None,
) -> _LiveMarketRequest:
    if dataset is not None:
        parsed = parse_market_dataset_id(dataset)
        live_kind = _live_kind(parsed.kind)
        if live_kind not in {"ticker", "orderbook", "trades", "option_greeks"}:
            raise ValueError(f"dataset does not map to a live market stream: {dataset}")
        return _LiveMarketRequest(
            dataset=parsed.dataset_id,
            dataset_kind=parsed.kind,
            kind=live_kind,
            symbol=parsed.source_symbol,
            exchange_name=_exchange_name(parsed.venue),
            market=parsed.market,
        )
    if kind is None:
        raise ValueError("--kind is required when dataset is not provided")
    if symbol is None or not symbol.strip():
        raise ValueError("--symbol is required when dataset is not provided")
    live_kind = _live_kind(kind)
    if live_kind not in {"ticker", "orderbook", "trades", "option_greeks"}:
        raise ValueError(f"unsupported live market data kind: {kind}")
    return _LiveMarketRequest(
        dataset=None,
        dataset_kind=live_kind,
        kind=live_kind,
        symbol=symbol,
        exchange_name=exchange_name,
        market=_normalize_market(market or ("option" if live_kind == "option_greeks" else "spot")),
    )


def _historical_capabilities(market: str) -> tuple[dict[str, object], ...]:
    if market == "option":
        return ()
    return (
        {
            "kind": "ohlcv",
            "label": "bars",
            "selector": "Bar",
            "timeframe_required": True,
            "command_kind": "ohlcv",
        },
    )


def _live_capabilities(market: str) -> tuple[dict[str, object], ...]:
    capabilities = [
        {"kind": "ticker", "label": "quotes", "selector": "Quote", "command_kind": "ticker"},
        {"kind": "orderbook", "label": "orderbook", "selector": "OrderBookSnapshot", "command_kind": "orderbook"},
        {"kind": "trades", "label": "trades", "selector": "TradePrint", "command_kind": "trades"},
    ]
    if market == "option":
        capabilities.append({"kind": "option_greeks", "label": "option greeks", "selector": "OptionGreeks", "command_kind": "option_greeks"})
    return tuple(capabilities)


def _capability_supports(capability: Mapping[str, object], *, kind: str, data_mode: MarketDataMode) -> bool:
    if capability.get("status") != "configured":
        return False
    rows = capability.get(data_mode)
    return isinstance(rows, tuple) and _normalized_kind(kind) in {str(row.get("kind")) for row in rows if isinstance(row, Mapping)}


def _historical_kind(kind: str) -> str:
    value = _normalized_kind(kind)
    if value in {"bar", "bars", "ohlcv"}:
        return "ohlcv"
    return value


def _live_kind(kind: str) -> str:
    value = _normalized_kind(kind)
    if value in {"quote", "quotes", "ticker"}:
        return "ticker"
    if value in {"trade", "trades"}:
        return "trades"
    if value in {"option_greeks", "greeks", "option-greeks"}:
        return "option_greeks"
    return value


def _normalized_kind(kind: str) -> str:
    return str(kind).strip().lower()


def _normalize_venue(value: object) -> str:
    text = str(value).strip().lower()
    if text == "okex":
        return "okx"
    return text


def _normalize_market(value: object) -> str:
    text = str(value).strip().lower()
    aliases = {
        "linear": "swap",
        "perpetual": "swap",
        "perp": "swap",
        "futures": "future",
        "options": "option",
    }
    return aliases.get(text, text)


__all__ = ["MarketDataFacade"]
