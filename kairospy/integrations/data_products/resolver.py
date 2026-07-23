from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kairospy.data.ids import DatasetId
from kairospy.data.streams import DataStreamId, normalize_stream_id
from kairospy.integrations.data_products.catalog import BuiltInDataProductRegistry


PlanCapability = Literal["historical", "live", "both", "unknown"]


@dataclass(frozen=True, slots=True)
class DataProductPlan:
    target_stream: DataStreamId
    dataset_id: DatasetId
    provider: str | None
    venue: str | None
    product_key: str | None
    title: str
    capability: PlanCapability
    primary_time: str | None
    source: str
    source_plan: dict[str, object] | None = None

    def to_payload(self) -> dict[str, object]:
        return {
            "stream": str(self.target_stream),
            "space": str(self.target_stream.space),
            "name": self.target_stream.stream,
            "dataset": str(self.dataset_id),
            "provider": self.provider,
            "venue": self.venue,
            "product_key": self.product_key,
            "title": self.title,
            "capability": self.capability,
            "primary_time": self.primary_time,
            "source": self.source,
            "source_plan": self.source_plan or {},
        }


class DataProductResolver:
    """Resolve user-facing stream names into product/storage plans."""

    def __init__(self, registry: BuiltInDataProductRegistry | None = None) -> None:
        self.registry = registry or BuiltInDataProductRegistry.from_default_products()

    def resolve(self, value: object) -> DataProductPlan:
        text = str(value).strip()
        try:
            product = self.registry.resolve(text)
        except KeyError:
            product = None
        if product is not None:
            return DataProductPlan(
                target_stream=normalize_stream_id(product.default_dataset_name),
                dataset_id=DatasetId(product.default_dataset_name),
                provider=product.provider,
                venue=product.venue,
                product_key=product.key,
                title=product.title,
                capability=product.capability,
                primary_time=product.primary_time,
                source="legacy_product_key",
                source_plan={"product_key": product.key},
            )
        stream = normalize_stream_id(text)
        planned = _resolve_named_stream(stream)
        if planned is not None:
            return planned
        return DataProductPlan(
            target_stream=stream,
            dataset_id=DatasetId(str(stream)),
            provider=None,
            venue=None,
            product_key=None,
            title=f"User data stream {stream}",
            capability="unknown",
            primary_time=None,
            source="stream",
            source_plan={},
        )


def _resolve_named_stream(stream: DataStreamId) -> DataProductPlan | None:
    space, name = str(stream.space), stream.stream
    if space.startswith("binance_swap_"):
        symbol = space.removeprefix("binance_swap_").upper()
        return _binance_plan(stream, name, market="usdm-perpetual", symbol=symbol)
    if space.startswith("binance_spot_"):
        symbol = space.removeprefix("binance_spot_").upper()
        return _binance_plan(stream, name, market="spot", symbol=symbol)
    if space.startswith("hyperliquid_perp_"):
        coin = space.removeprefix("hyperliquid_perp_").upper()
        return _hyperliquid_plan(stream, name, coin=coin)
    return None


def _binance_plan(stream: DataStreamId, name: str, *, market: str, symbol: str) -> DataProductPlan | None:
    canonical_symbol = _hyphen_symbol(symbol)
    product_key = None
    kind = None
    primary_time = "event_time"
    capability: PlanCapability = "unknown"
    if name == "orderbook":
        product_key = "binance.orderbook"
        kind = "orderbook"
        capability = "live"
    elif name in {"trades", "trade"}:
        kind = "trade"
        capability = "live"
    elif name == "funding":
        kind = "funding"
        capability = "historical"
    elif name.startswith("ohlcv_"):
        interval = name.removeprefix("ohlcv_")
        kind = "ohlcv"
        primary_time = "period_start"
        capability = "historical"
        if market == "usdm-perpetual" and interval == "1h":
            product_key = "market.ohlcv.crypto.binance.usdm-perpetual.1h"
        if market == "spot" and interval == "1d" and canonical_symbol == "btc-usdt":
            product_key = "market.ohlcv.crypto.binance.btc-usdt.1d"
    if kind is None:
        return None
    suffix = f".{name.removeprefix('ohlcv_')}" if kind == "ohlcv" else ""
    dataset = DatasetId(f"market.{kind}.crypto.binance.{market}.{canonical_symbol}{suffix}")
    return DataProductPlan(
        target_stream=stream,
        dataset_id=dataset,
        provider="binance",
        venue="binance-usdm" if market == "usdm-perpetual" else "binance",
        product_key=product_key,
        title=f"Binance {market} {symbol} {name}",
        capability=capability,
        primary_time=primary_time,
        source="stream_product_rule",
        source_plan={
            "product_key": product_key,
            "instrument": symbol,
            "market": "usdm" if market == "usdm-perpetual" else market,
            "fanout_target_stream": str(stream) if product_key and product_key != str(dataset) else None,
        },
    )


def _hyperliquid_plan(stream: DataStreamId, name: str, *, coin: str) -> DataProductPlan | None:
    product_key = None
    kind = None
    primary_time = "event_time"
    capability: PlanCapability = "unknown"
    if name == "orderbook":
        product_key = "hyperliquid.perpetual.orderbook"
        kind = "orderbook"
        capability = "live"
    elif name in {"trades", "trade"}:
        product_key = "hyperliquid.perpetual.trade"
        kind = "trade"
        capability = "live"
    elif name == "funding":
        product_key = "hyperliquid.perpetual.funding"
        kind = "funding"
        capability = "both"
    elif name.startswith("ohlcv_"):
        interval = name.removeprefix("ohlcv_")
        product_key = f"hyperliquid.perpetual.ohlcv.{interval}"
        kind = "ohlcv"
        primary_time = "period_start"
        capability = "both" if interval == "1m" else "historical"
    if kind is None:
        return None
    suffix = f".{name.removeprefix('ohlcv_')}" if kind == "ohlcv" else ""
    dataset = DatasetId(f"market.{kind}.crypto.hyperliquid.perpetual.{_segment(coin)}{suffix}")
    return DataProductPlan(
        target_stream=stream,
        dataset_id=dataset,
        provider="hyperliquid",
        venue="hyperliquid",
        product_key=product_key,
        title=f"Hyperliquid perpetual {coin} {name}",
        capability=capability,
        primary_time=primary_time,
        source="stream_product_rule",
        source_plan={
            "product_key": product_key,
            "instrument": coin,
        },
    )


def _hyphen_symbol(symbol: str) -> str:
    lowered = "".join(character for character in symbol.lower() if character.isalnum())
    for quote in ("fdusd", "usdt", "usdc", "busd", "tusd", "btc", "eth", "bnb", "usd", "eur", "try"):
        if lowered.endswith(quote) and len(lowered) > len(quote):
            return f"{lowered[:-len(quote)]}-{quote}"
    return lowered


def _segment(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value).strip("-")
