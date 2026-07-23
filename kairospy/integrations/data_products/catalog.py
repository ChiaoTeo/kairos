from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Iterable, Literal, Mapping

from kairospy.data.contracts import DataProductContract, DatasetLayer
from kairospy.data.protocols import DataProtocolRegistry, HistoricalDataRequest, LiveDataRequest


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
            raise KeyError(f"unknown integration-provided data product: {key}") from error

    def aliases(self) -> Mapping[str, str]:
        return dict(self._aliases)

    @classmethod
    def from_default_products(cls) -> BuiltInDataProductRegistry:
        from kairospy.integrations.data_products import KNOWN_PRODUCTS

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
        from kairospy.data.contracts import OutputFormat

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
    def __init__(self, product: BuiltInDataProduct, root: str | Path | None = None) -> None:
        self.product = product
        self.root = Path(root) if root is not None else None

    def runtime_config(self, request: LiveDataRequest) -> Mapping[str, object]:
        from kairospy.integrations.data_products.live_runtime import provider_live_runtime_config

        value = provider_live_runtime_config(self.product, request)
        return {} if value is None else value

    async def stream(self, request: LiveDataRequest) -> AsyncIterator[Mapping[str, object]]:
        from kairospy.integrations.data_products.live_stream import provider_live_stream

        config = self.runtime_config(request)
        if not config.get("provider"):
            raise RuntimeError(f"built-in live data protocol {self.product.protocol_name!r} is configured but not running")
        async for event in provider_live_stream(request, config):
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
            registry.register_live(product.protocol_name, BuiltInLiveDataProtocol(product, root))
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
