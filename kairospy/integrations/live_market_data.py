from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.identity import InstrumentId
from kairospy.infrastructure.configuration import KairosProjectConfig
from kairospy.integrations.config import resolve_ccxt_exchange_settings, resolve_provider_service_config


@dataclass(frozen=True, slots=True)
class LiveMarketDataRequest:
    dataset_id: str
    product_key: str
    provider: str
    instruments: tuple[str, ...]
    channel: str | None = None
    params: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class LiveMarketDataSourceConfig:
    provider: str
    service: str
    driver: str
    runtime_config: dict[str, object]


def resolve_live_market_data_source_config(
    config: KairosProjectConfig,
    request: LiveMarketDataRequest,
    *,
    service: str = "live_market_data",
) -> LiveMarketDataSourceConfig | None:
    service_config = resolve_provider_service_config(config, request.provider, service)
    values = service_config.values or {}
    driver = str(values.get("driver") or "").strip().lower()
    if not driver:
        return None
    if driver in {"ccxt-pro", "ccxt_pro", "ccxt.pro"}:
        return _ccxt_pro_source_config(config, request, service)
    raise ValueError(f"unsupported live market data driver for {request.provider}: {driver}")


def build_live_market_data_event_source(source_config: LiveMarketDataSourceConfig):
    if source_config.driver != "ccxt-pro":
        raise ValueError(f"unsupported live market data event source driver: {source_config.driver}")
    from kairospy.integrations.config import CcxtExchangeSettings
    from kairospy.integrations.connectors.ccxt import CcxtOrderBookEventSource, CcxtSymbolMapper, build_ccxt_pro_exchange

    runtime = source_config.runtime_config
    exchange = build_ccxt_pro_exchange(CcxtExchangeSettings(
        str(runtime["exchange_id"]),
        sandbox=bool(runtime.get("sandbox", False)),
        timeout_ms=int(runtime["timeout_ms"]) if runtime.get("timeout_ms") is not None else None,
        options=dict(runtime.get("options") or {}),
    ))
    instrument_id = InstrumentId(str(runtime["instrument_id"]))
    return CcxtOrderBookEventSource.for_instruments(
        exchange,
        provider=source_config.provider,
        instrument_ids=(instrument_id,),
        symbol_mapper=CcxtSymbolMapper({instrument_id: str(runtime["symbol"])}),
        depth=int(runtime["levels"]) if runtime.get("levels") is not None else None,
        new_updates=bool(runtime.get("new_updates", False)),
    )


def _ccxt_pro_source_config(
    config: KairosProjectConfig,
    request: LiveMarketDataRequest,
    service: str,
) -> LiveMarketDataSourceConfig:
    settings = resolve_ccxt_exchange_settings(config, request.provider, service)
    channel = _channel(request)
    if channel != "orderbook":
        raise ValueError(f"ccxt-pro live market data currently supports orderbook, got {channel!r}")
    symbol = _ccxt_symbol(settings.exchange_id, request)
    market = str((request.params or {}).get("market") or _default_market(settings.exchange_id))
    instrument_id = str((request.params or {}).get("instrument_id") or f"crypto:{settings.exchange_id}:{market}:{_safe_symbol(symbol)}")
    runtime_config = {
        "provider": request.provider,
        "venue": request.provider,
        "service": service,
        "driver": "ccxt-pro",
        "exchange_id": settings.exchange_id,
        "sandbox": settings.sandbox,
        "timeout_ms": settings.timeout_ms,
        "options": settings.options or {},
        "market": market,
        "symbol": symbol,
        "channel": "orderbook",
        "instrument_id": instrument_id,
        "source_instance": f"kairospy-data:{request.dataset_id}",
        "event_source_contract": "EventSource[DataSetRecord]",
        "channel_contract": "BoundedEventChannel",
    }
    levels = (request.params or {}).get("levels")
    if levels is not None:
        runtime_config["levels"] = int(levels)
    new_updates = (request.params or {}).get("new_updates")
    if new_updates is not None:
        runtime_config["new_updates"] = bool(new_updates)
    return LiveMarketDataSourceConfig(request.provider, service, "ccxt-pro", runtime_config)


def _channel(request: LiveMarketDataRequest) -> str:
    raw = str(request.channel or (request.params or {}).get("channel") or "")
    if raw in {"", "depth", "book", "order_book", "orderbook", "l2Book"}:
        return "orderbook"
    return raw


def _ccxt_symbol(exchange_id: str, request: LiveMarketDataRequest) -> str:
    params = request.params or {}
    explicit = params.get("symbol")
    if explicit:
        return str(explicit)
    if len(request.instruments) != 1:
        raise ValueError("ccxt-pro live market data requires exactly one instrument")
    raw = str(request.instruments[0]).strip()
    if not raw:
        raise ValueError("ccxt-pro live market data instrument cannot be empty")
    normalized_exchange = {"okex": "okx"}.get(exchange_id, exchange_id)
    if normalized_exchange == "hyperliquid":
        upper = raw.upper()
        if "/" in upper or ":" in upper:
            return upper
        return f"{upper}/USDC:USDC"
    if "/" in raw:
        return raw.upper()
    upper = "".join(character for character in raw.upper() if character.isalnum())
    for quote in ("USDT", "USDC", "USD", "BTC", "ETH", "BNB", "EUR"):
        if upper.endswith(quote) and len(upper) > len(quote):
            return f"{upper[:-len(quote)]}/{quote}"
    return upper


def _default_market(exchange_id: str) -> str:
    return "perpetual" if exchange_id == "hyperliquid" else "spot"


def _safe_symbol(symbol: str) -> str:
    return symbol.replace("/", "-").replace(":", "-").lower()


__all__ = [
    "LiveMarketDataRequest",
    "LiveMarketDataSourceConfig",
    "build_live_market_data_event_source",
    "resolve_live_market_data_source_config",
]
