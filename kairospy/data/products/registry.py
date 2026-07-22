from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import AsyncIterator, Iterable, Literal, Mapping

from ..contracts import DataProductContract, DatasetLayer
from ..protocols import DataProtocolRegistry, HistoricalDataRequest, LiveDataRequest


BuiltInSourceKind = Literal["built_in"]
BuiltInCapability = Literal["historical", "live", "both"]


BUILT_IN_PRODUCT_ALIASES = {
    "massive.equity.ohlcv.1d": "market.ohlcv.equity.us.massive.1d.vendor_adjusted",
    "massive.equity.ohlcv.1h": "market.ohlcv.equity.us.massive.1h.adjusted",
    "massive.option.ohlcv.1h": "market.ohlcv.option.us.massive.1h.raw",
    "massive.options.ohlcv.1h": "market.ohlcv.option.us.massive.1h.raw",
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


BUILT_IN_HYPERLIQUID_PRODUCTS = (
    BuiltInDataProduct(
        key="hyperliquid.perpetual.trade",
        title="Hyperliquid perpetual trades",
        capability="live",
        default_dataset_name="market.trade.crypto.hyperliquid.perpetual",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="event_time",
        protocol_name="built_in.live.hyperliquid.perpetual.trade",
        provider="hyperliquid",
        venue="hyperliquid",
    ),
    BuiltInDataProduct(
        key="hyperliquid.perpetual.orderbook",
        title="Hyperliquid perpetual order book",
        capability="live",
        default_dataset_name="market.orderbook.crypto.hyperliquid.perpetual",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="event_time",
        protocol_name="built_in.live.hyperliquid.perpetual.orderbook",
        provider="hyperliquid",
        venue="hyperliquid",
    ),
    BuiltInDataProduct(
        key="hyperliquid.perpetual.funding",
        title="Hyperliquid perpetual funding",
        capability="both",
        default_dataset_name="market.funding.crypto.hyperliquid.perpetual",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="event_time",
        protocol_name="built_in.hyperliquid.perpetual.funding",
        provider="hyperliquid",
        venue="hyperliquid",
    ),
    BuiltInDataProduct(
        key="hyperliquid.perpetual.ohlcv.1m",
        title="Hyperliquid perpetual 1 minute candles",
        capability="both",
        default_dataset_name="market.ohlcv.crypto.hyperliquid.perpetual.1m",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="period_start",
        protocol_name="built_in.hyperliquid.perpetual.ohlcv.1m",
        provider="hyperliquid",
        venue="hyperliquid",
    ),
    BuiltInDataProduct(
        key="hyperliquid.perpetual.ohlcv.1h",
        title="Hyperliquid perpetual 1 hour candles",
        capability="historical",
        default_dataset_name="market.ohlcv.crypto.hyperliquid.perpetual.1h",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="period_start",
        protocol_name="built_in.historical.hyperliquid.perpetual.ohlcv.1h",
        provider="hyperliquid",
        venue="hyperliquid",
    ),
)


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
    BuiltInDataProduct(
        key="massive.trade",
        title="Massive real-time trades",
        capability="live",
        default_dataset_name="market.trade.us_equity.massive",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="event_time",
        protocol_name="built_in.live.massive.trade",
        provider="massive",
        venue="us-securities",
        requires_account=True,
    ),
    BuiltInDataProduct(
        key="massive.quote",
        title="Massive real-time quotes",
        capability="live",
        default_dataset_name="market.quote.us_equity.massive",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="event_time",
        protocol_name="built_in.live.massive.quote",
        provider="massive",
        venue="us-securities",
        requires_account=True,
    ),
    BuiltInDataProduct(
        key="massive.aggregate",
        title="Massive real-time aggregate bars",
        capability="live",
        default_dataset_name="market.ohlcv.us_equity.massive.1m",
        layer=DatasetLayer.CANONICAL.value,
        primary_time="period_start",
        protocol_name="built_in.live.massive.aggregate",
        provider="massive",
        venue="us-securities",
        requires_account=True,
    ),
)


BUILT_IN_EXTRA_PRODUCTS = BUILT_IN_LIVE_PRODUCTS + BUILT_IN_HYPERLIQUID_PRODUCTS


def built_in_dataset_id(product: BuiltInDataProduct, *, instruments: Iterable[str] = (),
                        params: Mapping[str, object] | None = None) -> str:
    """Return the canonical Dataset ID for a built-in product request."""

    values = tuple(str(item) for item in instruments)
    request_params = params or {}
    if product.protocol_name in {"built_in.live.binance.quote", "built_in.live.binance.orderbook"}:
        if len(values) != 1:
            raise ValueError("built-in Binance live source requires exactly one --instrument")
        kind = "orderbook" if product.protocol_name == "built_in.live.binance.orderbook" else "quote"
        market = _binance_market(request_params.get("market"))
        symbol = _canonical_binance_symbol(_binance_symbol(values[0]))
        return f"market.{kind}.crypto.binance.{market}.{symbol}"
    if product.provider == "massive" and product.capability in {"live", "both"}:
        if len(values) != 1:
            raise ValueError("built-in Massive live source requires exactly one --instrument")
        symbol = _canonical_segment(_massive_symbol(values[0]))
        if product.protocol_name == "built_in.live.massive.trade":
            return f"market.trade.us_equity.massive.{symbol}"
        if product.protocol_name == "built_in.live.massive.quote":
            return f"market.quote.us_equity.massive.{symbol}"
        if product.protocol_name == "built_in.live.massive.aggregate":
            interval = _massive_interval(request_params.get("interval"))
            return f"market.ohlcv.us_equity.massive.{interval}.{symbol}"
    if product.provider == "hyperliquid":
        if "ohlcv" in product.protocol_name:
            interval = _hyperliquid_interval_from_product(product)
            return f"market.ohlcv.crypto.hyperliquid.perpetual.{interval}"
        if len(values) != 1:
            raise ValueError("built-in Hyperliquid live/funding source requires exactly one --instrument")
        coin = _canonical_segment(_hyperliquid_coin(values[0]))
        if "trade" in product.protocol_name:
            return f"market.trade.crypto.hyperliquid.perpetual.{coin}"
        if "orderbook" in product.protocol_name:
            return f"market.orderbook.crypto.hyperliquid.perpetual.{coin}"
        if "funding" in product.protocol_name:
            return f"market.funding.crypto.hyperliquid.perpetual.{coin}"
    return product.default_dataset_name


class BuiltInDataProductRegistry:
    """User-facing index of built-in Data products backed by internal contracts."""

    _VISIBLE_LAYERS = {DatasetLayer.SOURCE, DatasetLayer.CANONICAL, DatasetLayer.REFERENCE}

    def __init__(self, products: Iterable[DataProductContract],
                 live_products: Iterable[BuiltInDataProduct] = BUILT_IN_EXTRA_PRODUCTS) -> None:
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
        from .builtin import KNOWN_PRODUCTS

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
    def __init__(self, root: str | Path, product: BuiltInDataProduct) -> None:
        self.root = Path(root)
        self.product = product

    def load(self, request: HistoricalDataRequest):
        from kairospy.data.storage.client import DatasetClient
        from ..contracts import OutputFormat

        return DatasetClient(self.root).read(
            request.dataset_id or self.product.default_dataset_name,
            start=request.start,
            end=request.end,
            instruments=request.instruments,
            output=OutputFormat.ROWS,
        )

    def plan(self, request: HistoricalDataRequest):
        raise RuntimeError("built-in historical provider ingestion has not been migrated to Dataset Store")

    def prepare(self, request: HistoricalDataRequest, *, dry_run: bool = False):
        raise RuntimeError("built-in historical provider ingestion has not been migrated to Dataset Store")

    def _client(self):
        from kairospy.data.storage.client import DatasetClient

        return DatasetClient(self.root)


class BuiltInLiveDataProtocol:
    def __init__(self, product: BuiltInDataProduct) -> None:
        self.product = product

    def runtime_config(self, request: LiveDataRequest) -> Mapping[str, object]:
        if self.product.protocol_name in {"built_in.live.binance.quote", "built_in.live.binance.orderbook"}:
            return _binance_quote_runtime_config(
                request,
                default_channel="depth" if self.product.protocol_name == "built_in.live.binance.orderbook" else None,
            )
        if self.product.provider == "massive":
            return _massive_runtime_config(self.product, request)
        if self.product.provider == "hyperliquid":
            return _hyperliquid_runtime_config(self.product, request)
        return {}

    async def stream(self, request: LiveDataRequest) -> AsyncIterator[Mapping[str, object]]:
        config = self.runtime_config(request)
        if self.product.protocol_name in {"built_in.live.binance.quote", "built_in.live.binance.orderbook"}:
            source = _binance_quote_stream(request, config)
        elif self.product.provider == "massive":
            source = _massive_live_stream(request, config)
        elif self.product.provider == "hyperliquid":
            source = _hyperliquid_live_stream(request, config)
        else:
            raise RuntimeError(f"built-in live data protocol {self.product.protocol_name!r} is configured but not running")
        async for event in source:
            yield event


def default_builtin_protocol_registry(
    root: str | Path,
    products: Iterable[BuiltInDataProduct],
) -> DataProtocolRegistry:
    registry = DataProtocolRegistry()
    for product in products:
        if product.capability in {"historical", "both"}:
            registry.register_historical(
                product.protocol_name,
                BuiltInHistoricalDataProtocol(root, product),
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


def _massive_runtime_config(product: BuiltInDataProduct, request: LiveDataRequest) -> Mapping[str, object]:
    if len(request.instruments) != 1:
        raise ValueError("built-in Massive live source requires exactly one --instrument")
    symbol = _massive_symbol(request.instruments[0])
    market = _massive_market(request.params.get("market"))
    if product.protocol_name == "built_in.live.massive.quote":
        event, channel = "Q", "quote"
        primary_time = "event_time"
    elif product.protocol_name == "built_in.live.massive.trade":
        event, channel = "T", "trade"
        primary_time = "event_time"
    elif product.protocol_name == "built_in.live.massive.aggregate":
        event, channel = "AM", "aggregate"
        primary_time = "period_start"
    else:
        raise ValueError(f"unsupported Massive live product: {product.key}")
    subscription = f"{event}.{symbol}"
    interval = _massive_interval(request.params.get("interval")) if channel == "aggregate" else None
    return {
        "provider": "massive",
        "venue": "us-securities",
        "market": market,
        "symbol": symbol,
        "channel": channel,
        **({"interval": interval} if interval is not None else {}),
        "subscription": subscription,
        "subscriptions": [subscription],
        "instrument_id": str(request.params.get("instrument_id") or f"equity:us:massive:{symbol}"),
        "primary_time": primary_time,
        "source_instance": f"kairospy-data:{request.dataset_id}",
        "event_source_contract": "EventSource[DataSetRecord]",
        "channel_contract": "BoundedEventChannel",
    }


def _hyperliquid_runtime_config(product: BuiltInDataProduct, request: LiveDataRequest) -> Mapping[str, object]:
    if "ohlcv" in product.protocol_name:
        coin = _single_hyperliquid_coin(request)
        interval = _hyperliquid_interval_from_product(product)
        subscription = {"type": "candle", "coin": coin, "interval": interval}
        channel = "candle"
        primary_time = "period_start"
    else:
        coin = _single_hyperliquid_coin(request)
        if "trade" in product.protocol_name:
            subscription = {"type": "trades", "coin": coin}
            channel = "trade"
            primary_time = "event_time"
        elif "orderbook" in product.protocol_name:
            subscription = {"type": "l2Book", "coin": coin}
            channel = "orderbook"
            primary_time = "event_time"
        elif "funding" in product.protocol_name:
            subscription = {"type": "activeAssetCtx", "coin": coin}
            channel = "funding"
            primary_time = "event_time"
        else:
            raise ValueError(f"unsupported Hyperliquid live product: {product.key}")
    return {
        "provider": "hyperliquid",
        "venue": "hyperliquid",
        "market": "perpetual",
        "coin": coin,
        "symbol": coin,
        "channel": channel,
        "subscription": subscription,
        "instrument_id": str(request.params.get("instrument_id") or f"crypto:hyperliquid:perpetual:{coin}"),
        "primary_time": primary_time,
        "source_instance": f"kairospy-data:{request.dataset_id}",
        "event_source_contract": "EventSource[DataSetRecord]",
        "channel_contract": "BoundedEventChannel",
    }


def _single_hyperliquid_coin(request: LiveDataRequest) -> str:
    if len(request.instruments) != 1:
        raise ValueError("built-in Hyperliquid live source requires exactly one --instrument")
    return _hyperliquid_coin(request.instruments[0])


def _binance_symbol(value: str) -> str:
    symbol = "".join(character for character in str(value).upper() if character.isalnum())
    if not symbol:
        raise ValueError("built-in live source 'binance.quote' requires a non-empty --instrument")
    return symbol


def _canonical_binance_symbol(symbol: str) -> str:
    lowered = symbol.lower()
    for quote in ("fdusd", "usdt", "usdc", "busd", "tusd", "btc", "eth", "bnb", "usd", "eur", "try"):
        if lowered.endswith(quote) and len(lowered) > len(quote):
            return f"{lowered[:-len(quote)]}-{quote}"
    return lowered


def _massive_symbol(value: str) -> str:
    symbol = str(value).strip().upper()
    for prefix in ("US:", "EQUITY:", "MASSIVE:"):
        if symbol.startswith(prefix):
            symbol = symbol[len(prefix):]
    if not symbol:
        raise ValueError("built-in Massive live source requires a non-empty --instrument")
    return symbol


def _massive_market(value: object) -> str:
    raw = str(value or "stocks").strip().lower()
    aliases = {
        "stock": "stocks",
        "stocks": "stocks",
        "equity": "stocks",
        "equities": "stocks",
        "option": "options",
        "options": "options",
        "index": "indices",
        "indices": "indices",
        "forex": "forex",
        "crypto": "crypto",
        "futures": "futures",
    }
    try:
        return aliases[raw]
    except KeyError as error:
        raise ValueError("built-in Massive live source supports market stocks, options, indices, forex, crypto, or futures") from error


def _massive_interval(value: object) -> str:
    raw = str(value or "1m").strip().lower()
    aliases = {"1m": "1m", "minute": "1m", "1min": "1m"}
    try:
        return aliases[raw]
    except KeyError as error:
        raise ValueError("built-in Massive aggregate currently supports --interval 1m") from error


def _hyperliquid_coin(value: str) -> str:
    symbol = str(value).strip().upper()
    for prefix in ("CRYPTO:HYPERLIQUID:PERPETUAL:", "HYPERLIQUID:", "PERP:"):
        if symbol.startswith(prefix):
            symbol = symbol[len(prefix):]
    if "-" in symbol:
        symbol = symbol.split("-", 1)[0]
    if "/" in symbol:
        symbol = symbol.split("/", 1)[0]
    if not symbol or not any(character.isalnum() for character in symbol):
        raise ValueError("built-in Hyperliquid source requires a non-empty coin, for example BTC")
    return "".join(character for character in symbol if character.isalnum())


def _hyperliquid_interval_from_product(product: BuiltInDataProduct) -> str:
    if product.key.endswith(".1m") or product.protocol_name.endswith(".1m"):
        return "1m"
    if product.key.endswith(".1h") or product.protocol_name.endswith(".1h"):
        return "1h"
    return "1m"


def _canonical_segment(value: str) -> str:
    output: list[str] = []
    previous_dash = False
    for character in str(value).strip().lower():
        if character.isalnum():
            output.append(character)
            previous_dash = False
        elif not previous_dash:
            output.append("-")
            previous_dash = True
    result = "".join(output).strip("-")
    if not result:
        raise ValueError("empty Dataset selector")
    return result


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


async def _massive_live_stream(
    request: LiveDataRequest,
    config: Mapping[str, object],
) -> AsyncIterator[Mapping[str, object]]:
    source = request.params.get("message_source")
    if source is not None:
        async for message in _iter_messages(source):
            for row in _massive_rows(message, config):
                yield row
        return

    from kairospy.integrations.connectors.massive.config import MassiveConfig
    from kairospy.integrations.connectors.massive.websocket import MassiveWebSocketClient

    client = request.params.get("client")
    if client is None:
        massive_config = request.params.get("massive_config")
        if massive_config is None:
            massive_config = MassiveConfig.from_env()
        client = MassiveWebSocketClient(massive_config)
    async for batch in client.messages(str(config["market"]), tuple(config["subscriptions"])):
        for message in batch:
            for row in _massive_rows(message, config):
                yield row


async def _hyperliquid_live_stream(
    request: LiveDataRequest,
    config: Mapping[str, object],
) -> AsyncIterator[Mapping[str, object]]:
    source = request.params.get("message_source")
    if source is not None:
        async for message in _iter_messages(source):
            for row in _hyperliquid_rows(message, config):
                yield row
        return

    try:
        import websockets
    except ImportError as error:
        raise RuntimeError("Hyperliquid WebSocket requires the 'websockets' optional dependency") from error
    url = str(request.params.get("websocket_url") or "wss://api.hyperliquid.xyz/ws")
    async with websockets.connect(url) as socket:
        await socket.send(json.dumps({
            "method": "subscribe",
            "subscription": config["subscription"],
        }))
        async for raw in socket:
            message = json.loads(raw)
            for row in _hyperliquid_rows(message, config):
                yield row


async def _iter_messages(source) -> AsyncIterator[Mapping[str, object]]:
    value = source() if callable(source) else source
    if hasattr(value, "__aiter__"):
        async for item in value:
            if isinstance(item, list):
                for nested in item:
                    yield dict(nested)
            else:
                yield dict(item)
        return
    for item in value:
        if isinstance(item, list):
            for nested in item:
                yield dict(nested)
        else:
            yield dict(item)


def _massive_rows(message: Mapping[str, object], config: Mapping[str, object]) -> list[dict[str, object]]:
    event = str(message.get("ev") or "").upper()
    symbol = str(message.get("sym") or message.get("ticker") or config.get("symbol") or "")
    instrument_id = str(config["instrument_id"])
    if event == "Q" or config.get("channel") == "quote":
        return [{
            "kind": "quote",
            "event_time": _timestamp_iso(message.get("t") or message.get("timestamp")),
            "instrument_id": instrument_id,
            "symbol": symbol,
            "bid": _float_value(message.get("bp") or message.get("bid_price")),
            "ask": _float_value(message.get("ap") or message.get("ask_price")),
            "bid_size": _float_value(message.get("bs") or message.get("bid_size")),
            "ask_size": _float_value(message.get("as") or message.get("ask_size")),
            "sequence_number": message.get("q") or message.get("sequence_number"),
            "source": "massive",
        }]
    if event == "T" or config.get("channel") == "trade":
        return [{
            "kind": "trade",
            "event_time": _timestamp_iso(message.get("t") or message.get("timestamp")),
            "instrument_id": instrument_id,
            "symbol": symbol,
            "price": _float_value(message.get("p") or message.get("price")),
            "size": _float_value(message.get("s") or message.get("size")),
            "trade_id": message.get("i") or message.get("id"),
            "sequence_number": message.get("q") or message.get("sequence_number"),
            "source": "massive",
        }]
    if event == "AM" or config.get("channel") == "aggregate":
        period_start = _timestamp_iso(message.get("s") or message.get("start") or message.get("t"))
        period_end = _timestamp_iso(message.get("e") or message.get("end"))
        return [{
            "kind": "bar",
            "period_start": period_start,
            "period_end": period_end,
            "instrument_id": instrument_id,
            "symbol": symbol,
            "interval": str(config.get("interval") or "1m"),
            "open": _float_value(message.get("o") or message.get("open")),
            "high": _float_value(message.get("h") or message.get("high")),
            "low": _float_value(message.get("l") or message.get("low")),
            "close": _float_value(message.get("c") or message.get("close")),
            "volume": _float_value(message.get("v") or message.get("volume")),
            "source": "massive",
        }]
    return [{"kind": "raw", "event_time": _timestamp_iso(message.get("t")), "instrument_id": instrument_id, "symbol": symbol, "raw": dict(message)}]


def _hyperliquid_rows(message: Mapping[str, object], config: Mapping[str, object]) -> list[dict[str, object]]:
    channel = str(message.get("channel") or config.get("channel") or "")
    if channel == "subscriptionResponse":
        return []
    data = message.get("data", message)
    if channel == "trades" or config.get("channel") == "trade":
        values = data if isinstance(data, list) else [data]
        return [_hyperliquid_trade_row(item, config) for item in values if isinstance(item, Mapping)]
    if channel == "l2Book" or config.get("channel") == "orderbook":
        return [_hyperliquid_orderbook_row(data, config)] if isinstance(data, Mapping) else []
    if channel == "candle" or config.get("channel") == "candle":
        values = data if isinstance(data, list) else [data]
        return [_hyperliquid_candle_row(item, config) for item in values if isinstance(item, Mapping)]
    if channel == "activeAssetCtx" or config.get("channel") == "funding":
        return [_hyperliquid_funding_row(data, config)] if isinstance(data, Mapping) else []
    return [{"kind": "raw", "event_time": _now_iso(), "instrument_id": str(config["instrument_id"]), "raw": dict(message)}]


def _hyperliquid_trade_row(item: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": "trade",
        "event_time": _timestamp_iso(item.get("time") or item.get("timestamp")),
        "instrument_id": str(config["instrument_id"]),
        "coin": str(item.get("coin") or config.get("coin") or ""),
        "side": item.get("side"),
        "price": _float_value(item.get("px") or item.get("price")),
        "size": _float_value(item.get("sz") or item.get("size")),
        "trade_id": item.get("tid") or item.get("hash"),
        "source": "hyperliquid",
    }


def _hyperliquid_orderbook_row(data: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": "orderbook",
        "event_time": _timestamp_iso(data.get("time") or data.get("timestamp")),
        "instrument_id": str(config["instrument_id"]),
        "coin": str(data.get("coin") or config.get("coin") or ""),
        "levels": data.get("levels") or [],
        "source": "hyperliquid",
    }


def _hyperliquid_candle_row(item: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": "bar",
        "period_start": _timestamp_iso(item.get("t") or item.get("time")),
        "period_end": _timestamp_iso(item.get("T") or item.get("closeTime")),
        "instrument_id": str(config["instrument_id"]),
        "coin": str(item.get("s") or item.get("coin") or config.get("coin") or ""),
        "interval": str(item.get("i") or config.get("interval") or ""),
        "open": _float_value(item.get("o") or item.get("open")),
        "high": _float_value(item.get("h") or item.get("high")),
        "low": _float_value(item.get("l") or item.get("low")),
        "close": _float_value(item.get("c") or item.get("close")),
        "volume": _float_value(item.get("v") or item.get("volume")),
        "trade_count": item.get("n"),
        "source": "hyperliquid",
    }


def _hyperliquid_funding_row(data: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]:
    context = data.get("ctx") if isinstance(data.get("ctx"), Mapping) else data
    return {
        "kind": "funding",
        "event_time": _timestamp_iso(data.get("time") or data.get("timestamp")) if data.get("time") or data.get("timestamp") else _now_iso(),
        "instrument_id": str(config["instrument_id"]),
        "coin": str(data.get("coin") or config.get("coin") or ""),
        "funding_rate": _float_value(context.get("funding") or context.get("fundingRate")),
        "premium": _float_value(context.get("premium")),
        "open_interest": _float_value(context.get("openInterest")),
        "mark_price": _float_value(context.get("markPx") or context.get("markPrice")),
        "source": "hyperliquid",
    }


def _timestamp_iso(value: object) -> str:
    if value is None:
        return _now_iso()
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)):
        numeric = float(value)
        if numeric > 10_000_000_000_000_000:
            numeric /= 1_000_000_000
        elif numeric > 10_000_000_000_000:
            numeric /= 1_000_000
        elif numeric > 10_000_000_000:
            numeric /= 1_000
        result = datetime.fromtimestamp(numeric, tz=timezone.utc)
    else:
        text = str(value)
        if text.isdigit():
            return _timestamp_iso(int(text))
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_value(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _environment(value: object) -> "Environment":
    from kairospy.environment import Environment

    if isinstance(value, Environment):
        return value
    if value is None:
        return Environment.LIVE
    return Environment(str(value))
