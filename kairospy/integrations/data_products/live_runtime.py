from __future__ import annotations

from typing import Mapping


def provider_live_runtime_config(product, request) -> Mapping[str, object] | None:
    protocol_name = str(getattr(product, "protocol_name", ""))
    provider = str(getattr(product, "provider", "") or "")
    if protocol_name in {"built_in.live.binance.quote", "built_in.live.binance.orderbook"}:
        return binance_runtime_config(
            request,
            default_channel="depth" if protocol_name == "built_in.live.binance.orderbook" else None,
        )
    if provider == "massive":
        return massive_runtime_config(product, request)
    if provider == "hyperliquid":
        return hyperliquid_runtime_config(product, request)
    return None


def binance_runtime_config(request, *, default_channel: str | None = None) -> Mapping[str, object]:
    if len(request.instruments) != 1:
        raise ValueError("built-in Binance live source requires exactly one --instrument")
    symbol = binance_symbol(request.instruments[0])
    channel = binance_channel(request.channel or default_channel)
    market = binance_market(request.params.get("market"))
    levels = binance_orderbook_levels(request.params.get("levels"))
    interval = binance_orderbook_interval(request.params.get("interval"))
    stream = binance_stream(symbol, channel, levels=levels, interval=interval)
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


def massive_runtime_config(product, request) -> Mapping[str, object]:
    if len(request.instruments) != 1:
        raise ValueError("built-in Massive live source requires exactly one --instrument")
    symbol = massive_symbol(request.instruments[0])
    market = massive_market(request.params.get("market"))
    protocol_name = str(getattr(product, "protocol_name", ""))
    if protocol_name == "built_in.live.massive.quote":
        event, channel = "Q", "quote"
        primary_time = "event_time"
    elif protocol_name == "built_in.live.massive.trade":
        event, channel = "T", "trade"
        primary_time = "event_time"
    elif protocol_name == "built_in.live.massive.aggregate":
        event, channel = "AM", "aggregate"
        primary_time = "period_start"
    else:
        raise ValueError(f"unsupported Massive live product: {getattr(product, 'key', '')}")
    subscription = f"{event}.{symbol}"
    interval = massive_interval(request.params.get("interval")) if channel == "aggregate" else None
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


def hyperliquid_runtime_config(product, request) -> Mapping[str, object]:
    protocol_name = str(getattr(product, "protocol_name", ""))
    if "ohlcv" in protocol_name:
        coin = single_hyperliquid_coin(request)
        interval = hyperliquid_interval_from_product(product)
        subscription = {"type": "candle", "coin": coin, "interval": interval}
        channel = "candle"
        primary_time = "period_start"
    else:
        coin = single_hyperliquid_coin(request)
        if "trade" in protocol_name:
            subscription = {"type": "trades", "coin": coin}
            channel = "trade"
            primary_time = "event_time"
        elif "orderbook" in protocol_name:
            subscription = {"type": "l2Book", "coin": coin}
            channel = "orderbook"
            primary_time = "event_time"
        elif "funding" in protocol_name:
            subscription = {"type": "activeAssetCtx", "coin": coin}
            channel = "funding"
            primary_time = "event_time"
        else:
            raise ValueError(f"unsupported Hyperliquid live product: {getattr(product, 'key', '')}")
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


def single_hyperliquid_coin(request) -> str:
    if len(request.instruments) != 1:
        raise ValueError("built-in Hyperliquid live source requires exactly one --instrument")
    return hyperliquid_coin(request.instruments[0])


def binance_symbol(value: str) -> str:
    symbol = "".join(character for character in str(value).upper() if character.isalnum())
    if not symbol:
        raise ValueError("built-in live source 'binance.quote' requires a non-empty --instrument")
    return symbol


def canonical_binance_symbol(symbol: str) -> str:
    lowered = symbol.lower()
    for quote in ("fdusd", "usdt", "usdc", "busd", "tusd", "btc", "eth", "bnb", "usd", "eur", "try"):
        if lowered.endswith(quote) and len(lowered) > len(quote):
            return f"{lowered[:-len(quote)]}-{quote}"
    return lowered


def massive_symbol(value: str) -> str:
    symbol = str(value).strip().upper()
    for prefix in ("US:", "EQUITY:", "MASSIVE:"):
        if symbol.startswith(prefix):
            symbol = symbol[len(prefix):]
    if not symbol:
        raise ValueError("built-in Massive live source requires a non-empty --instrument")
    return symbol


def massive_market(value: object) -> str:
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


def massive_interval(value: object) -> str:
    raw = str(value or "1m").strip().lower()
    aliases = {"1m": "1m", "minute": "1m", "1min": "1m"}
    try:
        return aliases[raw]
    except KeyError as error:
        raise ValueError("built-in Massive aggregate currently supports --interval 1m") from error


def hyperliquid_coin(value: str) -> str:
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


def hyperliquid_interval_from_product(product) -> str:
    key = str(getattr(product, "key", ""))
    protocol_name = str(getattr(product, "protocol_name", ""))
    if key.endswith(".1m") or protocol_name.endswith(".1m"):
        return "1m"
    if key.endswith(".1h") or protocol_name.endswith(".1h"):
        return "1h"
    return "1m"


def binance_market(value: object) -> str:
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


def binance_channel(value: str | None) -> str:
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


def binance_orderbook_levels(value: object) -> int | None:
    if value is None:
        return None
    try:
        levels = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("Binance orderbook --levels must be 5, 10, or 20") from error
    if levels not in {5, 10, 20}:
        raise ValueError("Binance orderbook --levels must be 5, 10, or 20")
    return levels


def binance_orderbook_interval(value: object) -> str | None:
    if value is None:
        return None
    interval = str(value).strip().lower()
    if interval not in {"100ms", "1000ms"}:
        raise ValueError("Binance orderbook --interval must be 100ms or 1000ms")
    return interval


def binance_stream(symbol: str, channel: str, *, levels: int | None, interval: str | None) -> str:
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


__all__ = [
    "binance_runtime_config",
    "canonical_binance_symbol",
    "hyperliquid_runtime_config",
    "massive_runtime_config",
    "provider_live_runtime_config",
]
