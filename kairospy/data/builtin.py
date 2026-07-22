from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Iterable, Literal, Mapping

from .contracts import DataProductContract, DatasetLayer
from .protocols import DataProtocolRegistry, HistoricalDataRequest, LiveDataRequest


BuiltInSourceKind = Literal["built_in"]
BuiltInCapability = Literal["historical", "live", "both"]


BUILT_IN_PRODUCT_ALIASES = {
    "massive.equity.ohlcv.1d": "market.ohlcv.equity.us.massive.1d.vendor_adjusted",
}


@dataclass(frozen=True, slots=True)
class BuiltInDataProduct:
    key: str
    title: str
    capability: BuiltInCapability
    default_dataset_name: str
    layer: str
    primary_time: str
    protocol_name: str
    provider: str | None = None
    venue: str | None = None
    requires_account: bool = False
    source_kind: BuiltInSourceKind = "built_in"


BUILT_IN_LIVE_PRODUCTS = (
    BuiltInDataProduct(
        key="binance.orderbook",
        title="Binance order book stream",
        capability="live",
        default_dataset_name="market.orderbook.crypto.binance",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="event_time",
        protocol_name="built_in.live.binance.orderbook",
        provider="binance",
        venue="binance",
        requires_account=False,
    ),
    BuiltInDataProduct(
        key="binance.quote",
        title="Binance quote stream",
        capability="live",
        default_dataset_name="market.quote.crypto.binance",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="event_time",
        protocol_name="built_in.live.binance.quote",
        provider="binance",
        venue="binance",
        requires_account=False,
    ),
)


class BuiltInDataProductRegistry:
    """User-facing index of built-in Data products backed by internal contracts."""

    _VISIBLE_LAYERS = {DatasetLayer.SOURCE, DatasetLayer.CANONICAL, DatasetLayer.REFERENCE}

    def __init__(self, products: Iterable[DataProductContract],
                 live_products: Iterable[BuiltInDataProduct] = BUILT_IN_LIVE_PRODUCTS) -> None:
        values = [self._from_contract(item) for item in products if item.product.layer in self._VISIBLE_LAYERS]
        values.extend(live_products)
        self._products = tuple(sorted(values, key=lambda item: item.key))
        self._by_key = {item.key: item for item in self._products}
        self._aliases = {
            alias: target
            for alias, target in BUILT_IN_PRODUCT_ALIASES.items()
            if target in self._by_key
        }

    def list(self) -> tuple[BuiltInDataProduct, ...]:
        return self._products

    def resolve(self, key: str) -> BuiltInDataProduct:
        key = self._aliases.get(key, key)
        try:
            return self._by_key[key]
        except KeyError as error:
            raise KeyError(f"unknown built-in data product: {key}") from error

    def aliases(self) -> Mapping[str, str]:
        return dict(self._aliases)

    @classmethod
    def from_default_products(cls) -> BuiltInDataProductRegistry:
        from .bootstrap import KNOWN_PRODUCTS

        return cls(KNOWN_PRODUCTS)

    @staticmethod
    def _from_contract(spec: DataProductContract) -> BuiltInDataProduct:
        source = spec.product.sources[0] if spec.product.sources else None
        modes = set(source.acquisition_modes if source is not None else ())
        capability: BuiltInCapability = "live" if modes == {"stream"} else "historical"
        return BuiltInDataProduct(
            key=str(spec.key),
            title=spec.product.title,
            capability=capability,
            default_dataset_name=str(spec.key),
            layer=spec.product.layer.value,
            primary_time=spec.product.primary_time,
            protocol_name=f"built_in.historical.{spec.key}",
            provider=source.provider if source is not None else None,
            venue=source.venue if source is not None else None,
            requires_account=bool(source and source.provider in {"massive", "ibkr"}),
        )


class BuiltInHistoricalDataProtocol:
    def __init__(self, root: str | Path, product: BuiltInDataProduct,
                 *, connector_config: str | Path | None = None) -> None:
        self.root = Path(root)
        self.product = product
        self.connector_config = connector_config

    def load(self, request: HistoricalDataRequest):
        plan, release = self.prepare(request, dry_run=False)
        if release is None and not plan.complete:
            raise RuntimeError(f"built-in data product {self.product.key!r} is not ready")
        from .client import DatasetClient
        from .contracts import OutputFormat

        client = DatasetClient(self.root)
        query = client.get(
            self.product.default_dataset_name,
            start=request.start,
            end=request.end,
            instruments=request.instruments,
        )
        return query.collect(OutputFormat.ROWS)

    def plan(self, request: HistoricalDataRequest):
        if request.start is None or request.end is None:
            raise ValueError("built-in historical data requires start and end")
        client = self._client()
        return client.plan(
            self.product.default_dataset_name,
            start=_require_datetime(request.start, "start"),
            end=_require_datetime(request.end, "end"),
            provider=_optional_text(request.params.get("provider")),
            venue=_optional_text(request.params.get("venue")),
        )

    def prepare(self, request: HistoricalDataRequest, *, dry_run: bool = False):
        plan = self.plan(request)
        release = None
        aliases = _dataset_aliases(request.dataset_id, self.product.default_dataset_name)
        if not dry_run:
            release = self._client().acquire(
                plan,
                instruments=request.instruments,
                refresh=bool(request.params.get("refresh", False)),
                aliases=aliases,
            )
        return plan, release

    def _client(self):
        from .bootstrap import default_provider_registry, register_configured_products, register_default_products
        from .client import DatasetClient

        register_default_products(self.root)
        if self.connector_config is not None:
            register_configured_products(self.root, self.connector_config)
        providers = default_provider_registry(self.root, connector_config=self.connector_config)
        return DatasetClient(self.root, providers=providers)


class BuiltInLiveDataProtocol:
    def __init__(self, product: BuiltInDataProduct) -> None:
        self.product = product

    def runtime_config(self, request: LiveDataRequest) -> Mapping[str, object]:
        if self.product.protocol_name in {"built_in.live.binance.quote", "built_in.live.binance.orderbook"}:
            return _binance_quote_runtime_config(
                request,
                default_channel="depth" if self.product.protocol_name == "built_in.live.binance.orderbook" else None,
            )
        return {}

    async def stream(self, request: LiveDataRequest) -> AsyncIterator[Mapping[str, object]]:
        if self.product.protocol_name not in {"built_in.live.binance.quote", "built_in.live.binance.orderbook"}:
            raise RuntimeError(f"built-in live data protocol {self.product.protocol_name!r} is configured but not running")
        async for event in _binance_quote_stream(request, self.runtime_config(request)):
            yield event


def default_builtin_protocol_registry(
    root: str | Path,
    products: Iterable[BuiltInDataProduct],
    *,
    connector_config: str | Path | None = None,
) -> DataProtocolRegistry:
    registry = DataProtocolRegistry()
    for product in products:
        if product.capability in {"historical", "both"}:
            registry.register_historical(
                product.protocol_name,
                BuiltInHistoricalDataProtocol(root, product, connector_config=connector_config),
            )
        if product.capability in {"live", "both"}:
            registry.register_live(product.protocol_name, BuiltInLiveDataProtocol(product))
    return registry


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _require_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{label} must be datetime")
    return value


def _dataset_aliases(dataset_id: str, default_dataset_name: str) -> tuple[str, ...]:
    dataset = str(dataset_id).strip()
    if not dataset or dataset == default_dataset_name:
        return ()
    return (dataset,)


def _binance_quote_runtime_config(request: LiveDataRequest, *, default_channel: str | None = None) -> Mapping[str, object]:
    if len(request.instruments) != 1:
        raise ValueError("built-in Binance live source requires exactly one --instrument")
    symbol = _binance_symbol(request.instruments[0])
    channel = _binance_channel(request.channel or default_channel)
    market = _binance_market(request.params.get("market"))
    levels = _binance_orderbook_levels(request.params.get("levels"))
    interval = _binance_orderbook_interval(request.params.get("interval"))
    stream = _binance_stream(symbol, channel, levels=levels, interval=interval)
    futures = market == "usdm"
    return {
        "provider": "binance",
        "venue": "binance-usdm" if futures else "binance",
        "market": market,
        "symbol": symbol,
        "channel": channel,
        **({"levels": levels} if levels is not None else {}),
        **({"interval": interval} if interval is not None else {}),
        "stream": stream,
        "instrument_id": str(request.params.get("instrument_id") or f"crypto:binance:{market}:{symbol}"),
        "public_only": True,
        "futures": futures,
        "source_instance": f"kairospy-data:{request.dataset_id}",
        "event_source_contract": "EventSource[DataSetRecord]",
        "channel_contract": "BoundedEventChannel",
    }


def _binance_symbol(value: str) -> str:
    symbol = "".join(character for character in str(value).upper() if character.isalnum())
    if not symbol:
        raise ValueError("built-in live source 'binance.quote' requires a non-empty --instrument")
    return symbol


def _binance_market(value: object) -> str:
    raw = str(value or "spot").strip().lower()
    aliases = {
        "spot": "spot",
        "usdm": "usdm",
        "usd-m": "usdm",
        "futures": "usdm",
        "perp": "usdm",
        "perpetual": "usdm",
    }
    try:
        return aliases[raw]
    except KeyError as error:
        raise ValueError("built-in Binance live source supports --market spot or usdm") from error


def _binance_channel(value: str | None) -> str:
    raw = (value or "quote").strip()
    aliases = {
        "quote": "bookTicker",
        "book": "bookTicker",
        "book_ticker": "bookTicker",
        "bookTicker": "bookTicker",
        "trade": "trade",
        "aggTrade": "aggTrade",
        "agg_trade": "aggTrade",
        "depth": "depth",
    }
    try:
        return aliases[raw]
    except KeyError as error:
        raise ValueError("built-in live source 'binance.quote' supports quote, trade, aggTrade or depth channel") from error


def _binance_orderbook_levels(value: object) -> int | None:
    if value is None:
        return None
    try:
        levels = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Binance orderbook --levels must be 5, 10, or 20") from error
    if levels not in {5, 10, 20}:
        raise ValueError("Binance orderbook --levels must be 5, 10, or 20")
    return levels


def _binance_orderbook_interval(value: object) -> str | None:
    if value is None:
        return None
    interval = str(value).strip().lower()
    if interval not in {"100ms", "1000ms"}:
        raise ValueError("Binance orderbook --interval must be 100ms or 1000ms")
    return interval


def _binance_stream(symbol: str, channel: str, *, levels: int | None, interval: str | None) -> str:
    base = f"{symbol.lower()}@{channel}"
    if channel != "depth":
        if levels is not None or interval is not None:
            raise ValueError("Binance --levels and --interval are only supported for orderbook/depth streams")
        return base
    if levels is not None:
        base = f"{symbol.lower()}@depth{levels}"
    if interval is not None:
        base = f"{base}@{interval}"
    return base


async def _binance_quote_stream(
    request: LiveDataRequest,
    config: Mapping[str, object],
) -> AsyncIterator[Mapping[str, object]]:
    import asyncio

    from kairospy.integrations.connectors.binance.market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
    from kairospy.integrations.connectors.binance.stream import BinanceCanonicalStreamService
    from kairospy.identity import InstrumentId
    from kairospy.market.stream import BoundedEventChannel
    from kairospy.environment import Environment
    from kairospy.infrastructure.storage.codec import to_primitive

    connector = request.params.get("connector") or WebSocketClientConnector()
    environment = _environment(request.params.get("environment"))
    stream = str(config["stream"])
    output = BoundedEventChannel(int(request.params.get("channel_capacity") or 16))
    session = BinanceStreamSession(
        connector,
        websocket_url(
            environment,
            stream,
            futures=bool(config.get("futures")),
            public_only=bool(config.get("public_only")),
        ),
        maximum_reconnects=int(request.params.get("maximum_reconnects") or 5),
    )
    service = BinanceCanonicalStreamService(
        session,
        {str(config["symbol"]): InstrumentId(str(config["instrument_id"]))},
        output,
        source_instance=str(config["source_instance"]),
        stream_id=stream,
    )
    message_limit = request.params.get("message_limit")
    task = asyncio.create_task(service.run(
        message_limit=None if message_limit is None else int(message_limit),
    ))
    try:
        async for event in output.events():
            yield to_primitive(event)
        await task
    finally:
        session.stop()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _environment(value: object) -> "Environment":
    from kairospy.environment import Environment

    if isinstance(value, Environment):
        return value
    if value is None:
        return Environment.LIVE
    return Environment(str(value))
