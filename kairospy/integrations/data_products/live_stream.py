from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from typing import AsyncIterator, Mapping


async def provider_live_stream(request, config: Mapping[str, object]) -> AsyncIterator[Mapping[str, object]]:
    provider = str(config.get("provider") or "")
    if provider == "binance":
        source = binance_live_stream(request, config)
    elif provider == "massive":
        source = massive_live_stream(request, config)
    elif provider == "hyperliquid":
        source = hyperliquid_live_stream(request, config)
    else:
        raise RuntimeError(f"integration live stream provider {provider!r} is not configured")
    async for event in source:
        yield event


async def binance_live_stream(request, config: Mapping[str, object]) -> AsyncIterator[Mapping[str, object]]:
    from kairospy.environment import Environment
    from kairospy.identity import InstrumentId
    from kairospy.infrastructure.storage.codec import to_primitive
    from kairospy.integrations.connectors.binance.market_stream import (
        BinanceStreamSession,
        WebSocketClientConnector,
        websocket_url,
    )
    from kairospy.integrations.connectors.binance.stream import BinanceCanonicalStreamService
    from kairospy.market.stream import BoundedEventChannel

    connector = request.params.get("connector") or WebSocketClientConnector()
    environment = _environment(request.params.get("environment"), Environment)
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


async def massive_live_stream(request, config: Mapping[str, object]) -> AsyncIterator[Mapping[str, object]]:
    source = request.params.get("message_source")
    if source is not None:
        async for message in _iter_messages(source):
            for row in massive_rows(message, config):
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
            for row in massive_rows(message, config):
                yield row


async def hyperliquid_live_stream(request, config: Mapping[str, object]) -> AsyncIterator[Mapping[str, object]]:
    source = request.params.get("message_source")
    if source is not None:
        async for message in _iter_messages(source):
            for row in hyperliquid_rows(message, config):
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
            for row in hyperliquid_rows(message, config):
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


def massive_rows(message: Mapping[str, object], config: Mapping[str, object]) -> list[dict[str, object]]:
    event = str(message.get("ev") or "").upper()
    symbol = str(message.get("sym") or message.get("ticker") or config.get("symbol") or "")
    instrument_id = str(config["instrument_id"])
    if event == "Q" or config.get("channel") == "quote":
        return [{
            "kind": "quote",
            "event_time": timestamp_iso(message.get("t") or message.get("timestamp")),
            "instrument_id": instrument_id,
            "symbol": symbol,
            "bid": float_value(message.get("bp") or message.get("bid_price")),
            "ask": float_value(message.get("ap") or message.get("ask_price")),
            "bid_size": float_value(message.get("bs") or message.get("bid_size")),
            "ask_size": float_value(message.get("as") or message.get("ask_size")),
            "sequence_number": message.get("q") or message.get("sequence_number"),
            "source": "massive",
        }]
    if event == "T" or config.get("channel") == "trade":
        return [{
            "kind": "trade",
            "event_time": timestamp_iso(message.get("t") or message.get("timestamp")),
            "instrument_id": instrument_id,
            "symbol": symbol,
            "price": float_value(message.get("p") or message.get("price")),
            "size": float_value(message.get("s") or message.get("size")),
            "trade_id": message.get("i") or message.get("id"),
            "sequence_number": message.get("q") or message.get("sequence_number"),
            "source": "massive",
        }]
    if event == "AM" or config.get("channel") == "aggregate":
        period_start = timestamp_iso(message.get("s") or message.get("start") or message.get("t"))
        period_end = timestamp_iso(message.get("e") or message.get("end"))
        return [{
            "kind": "bar",
            "period_start": period_start,
            "period_end": period_end,
            "instrument_id": instrument_id,
            "symbol": symbol,
            "interval": str(config.get("interval") or "1m"),
            "open": float_value(message.get("o") or message.get("open")),
            "high": float_value(message.get("h") or message.get("high")),
            "low": float_value(message.get("l") or message.get("low")),
            "close": float_value(message.get("c") or message.get("close")),
            "volume": float_value(message.get("v") or message.get("volume")),
            "source": "massive",
        }]
    return [{"kind": "raw", "event_time": timestamp_iso(message.get("t")), "instrument_id": instrument_id, "symbol": symbol, "raw": dict(message)}]


def hyperliquid_rows(message: Mapping[str, object], config: Mapping[str, object]) -> list[dict[str, object]]:
    channel = str(message.get("channel") or config.get("channel") or "")
    if channel == "subscriptionResponse":
        return []
    data = message.get("data", message)
    if channel == "trades" or config.get("channel") == "trade":
        values = data if isinstance(data, list) else [data]
        return [hyperliquid_trade_row(item, config) for item in values if isinstance(item, Mapping)]
    if channel == "l2Book" or config.get("channel") == "orderbook":
        return [hyperliquid_orderbook_row(data, config)] if isinstance(data, Mapping) else []
    if channel == "candle" or config.get("channel") == "candle":
        values = data if isinstance(data, list) else [data]
        return [hyperliquid_candle_row(item, config) for item in values if isinstance(item, Mapping)]
    if channel == "activeAssetCtx" or config.get("channel") == "funding":
        return [hyperliquid_funding_row(data, config)] if isinstance(data, Mapping) else []
    return [{"kind": "raw", "event_time": now_iso(), "instrument_id": str(config["instrument_id"]), "raw": dict(message)}]


def hyperliquid_trade_row(item: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": "trade",
        "event_time": timestamp_iso(item.get("time") or item.get("timestamp")),
        "instrument_id": str(config["instrument_id"]),
        "coin": str(item.get("coin") or config.get("coin") or ""),
        "side": item.get("side"),
        "price": float_value(item.get("px") or item.get("price")),
        "size": float_value(item.get("sz") or item.get("size")),
        "trade_id": item.get("tid") or item.get("hash"),
        "source": "hyperliquid",
    }


def hyperliquid_orderbook_row(data: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": "orderbook",
        "event_time": timestamp_iso(data.get("time") or data.get("timestamp")),
        "instrument_id": str(config["instrument_id"]),
        "coin": str(data.get("coin") or config.get("coin") or ""),
        "levels": data.get("levels") or [],
        "source": "hyperliquid",
    }


def hyperliquid_candle_row(item: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]:
    return {
        "kind": "bar",
        "period_start": timestamp_iso(item.get("t") or item.get("time")),
        "period_end": timestamp_iso(item.get("T") or item.get("closeTime")),
        "instrument_id": str(config["instrument_id"]),
        "coin": str(item.get("s") or item.get("coin") or config.get("coin") or ""),
        "interval": str(item.get("i") or config.get("interval") or ""),
        "open": float_value(item.get("o") or item.get("open")),
        "high": float_value(item.get("h") or item.get("high")),
        "low": float_value(item.get("l") or item.get("low")),
        "close": float_value(item.get("c") or item.get("close")),
        "volume": float_value(item.get("v") or item.get("volume")),
        "trade_count": item.get("n"),
        "source": "hyperliquid",
    }


def hyperliquid_funding_row(data: Mapping[str, object], config: Mapping[str, object]) -> dict[str, object]:
    context = data.get("ctx") if isinstance(data.get("ctx"), Mapping) else data
    return {
        "kind": "funding",
        "event_time": timestamp_iso(data.get("time") or data.get("timestamp")) if data.get("time") or data.get("timestamp") else now_iso(),
        "instrument_id": str(config["instrument_id"]),
        "coin": str(data.get("coin") or config.get("coin") or ""),
        "funding_rate": float_value(context.get("funding") or context.get("fundingRate")),
        "premium": float_value(context.get("premium")),
        "open_interest": float_value(context.get("openInterest")),
        "mark_price": float_value(context.get("markPx") or context.get("markPrice")),
        "source": "hyperliquid",
    }


def timestamp_iso(value: object) -> str:
    if value is None:
        return now_iso()
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
            return timestamp_iso(int(text))
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc).isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def float_value(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _environment(value: object, environment_type):
    if isinstance(value, environment_type):
        return value
    if value is None:
        return environment_type.LIVE
    return environment_type(str(value))


__all__ = [
    "binance_live_stream",
    "float_value",
    "hyperliquid_live_stream",
    "hyperliquid_rows",
    "massive_live_stream",
    "massive_rows",
    "provider_live_stream",
    "timestamp_iso",
]
