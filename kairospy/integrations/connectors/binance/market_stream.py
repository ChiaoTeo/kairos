"""Binance public market stream utilities and reconnecting stream sessions."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from threading import Event, Lock
from time import time
from typing import Any, Protocol

from kairospy.environment import Environment
from kairospy.identity import InstrumentId
from kairospy.market.types import (
    DerivativeMarketState,
    OrderBookDelta,
    OrderBookLevel,
    OrderBookSnapshot,
    Quote,
    Trade,
)


class WebSocketConnection(Protocol):
    def receive(self) -> str | dict[str, Any]: ...
    def close(self) -> None: ...


class WebSocketConnector(Protocol):
    def connect(self, url: str) -> WebSocketConnection: ...


class WebSocketClientConnection:
    def __init__(self, socket) -> None:
        self.socket = socket

    def receive(self):
        return self.socket.recv()

    def close(self) -> None:
        self.socket.close()


class WebSocketClientConnector:
    """Concrete connector kept behind the stream protocol for deterministic tests."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def connect(self, url: str) -> WebSocketClientConnection:
        import websocket
        return WebSocketClientConnection(websocket.create_connection(url, timeout=self.timeout))


def websocket_url(environment: Environment, stream: str, *, futures: bool = False,
                  options: bool = False, public_only: bool = False) -> str:
    if options and futures:
        raise ValueError("Binance websocket URL cannot be both futures and options")
    if options:
        if environment is Environment.TESTNET:
            raise ValueError("Binance options user streams do not expose a supported testnet websocket endpoint")
        host = "wss://nbstream.binance.com/eoptions/ws"
        return f"{host}/{stream}"
    if futures:
        host = "wss://stream.binancefuture.com/ws" if environment is Environment.TESTNET else "wss://fstream.binance.com/ws"
    else:
        # Binance supports both 9443 and standard TLS port 443. Prefer 443 because
        # corporate and cloud egress policies commonly block the alternate port.
        host = "wss://testnet.binance.vision/ws" if environment is Environment.TESTNET else (
            "wss://data-stream.binance.vision/ws" if public_only else "wss://stream.binance.com/ws"
        )
    return f"{host}/{stream}"


def parse_market_stream_event(row: dict[str, Any], instrument_lookup: dict[str, InstrumentId]):
    payload = row.get("data", row)
    event_type = payload.get("e")
    if event_type is None:
        if all(key in payload for key in ("b", "a", "B", "A")) and not isinstance(payload.get("b"), list):
            event_type = "bookTicker"
        elif all(key in payload for key in ("U", "u", "b", "a")):
            event_type = "depthUpdate"
        elif all(key in payload for key in ("lastUpdateId", "bids", "asks")):
            event_type = "partialDepth"
        elif all(key in payload for key in ("p", "q", "t")):
            event_type = "trade"
    symbol = payload.get("s") or payload.get("symbol")
    if symbol is None and len(instrument_lookup) == 1:
        symbol = next(iter(instrument_lookup))
    if symbol not in instrument_lookup:
        raise LookupError(f"unknown Binance stream symbol: {symbol}")
    instrument_id = instrument_lookup[symbol]
    event_time = datetime.fromtimestamp(int(payload.get("E", int(time() * 1000))) / 1000, timezone.utc)
    if event_type == "bookTicker":
        return Quote(
            instrument_id, _decimal(payload.get("b")), _decimal(payload.get("a")),
            _decimal(payload.get("B")), _decimal(payload.get("A")), event_time,
        )
    if event_type in {"trade", "aggTrade"}:
        return Trade(instrument_id, Decimal(payload["p"]), Decimal(payload["q"]), event_time)
    if event_type == "partialDepth":
        return OrderBookSnapshot(
            instrument_id,
            tuple(OrderBookLevel(Decimal(price), Decimal(quantity)) for price, quantity in payload.get("bids", [])),
            tuple(OrderBookLevel(Decimal(price), Decimal(quantity)) for price, quantity in payload.get("asks", [])),
            int(payload["lastUpdateId"]),
            event_time,
        )
    if event_type == "depthUpdate":
        return OrderBookDelta(
            instrument_id,
            tuple(OrderBookLevel(Decimal(price), Decimal(quantity)) for price, quantity in payload.get("b", [])),
            tuple(OrderBookLevel(Decimal(price), Decimal(quantity)) for price, quantity in payload.get("a", [])),
            int(payload["U"]), int(payload["u"]), event_time,
        )
    if event_type == "markPriceUpdate":
        next_funding = payload.get("T")
        return DerivativeMarketState(
            instrument_id, _decimal(payload.get("i")), _decimal(payload.get("p")),
            _decimal(payload.get("r")),
            datetime.fromtimestamp(int(next_funding) / 1000, timezone.utc) if next_funding else None,
            _decimal(payload.get("o")), event_time,
        )
    return None


class BinanceStreamSession:
    """Injectable reconnecting stream loop; recovery callbacks perform REST backfill."""

    def __init__(self, connector: WebSocketConnector, url: str, *, maximum_reconnects: int = 5,
                 journal: str | Path | None = None) -> None:
        self.connector, self.url, self.maximum_reconnects = connector, url, maximum_reconnects
        self.journal = Path(journal) if journal is not None else None
        self._stop = Event()
        self._connection = None
        self._connection_lock = Lock()

    def consume(self, handler, *, message_limit: int | None = None, on_reconnect=None) -> int:
        if message_limit is not None and message_limit < 1:
            raise ValueError("message_limit must be positive")
        self._stop.clear()
        received = reconnects = 0
        connection = None
        try:
            while not self._stop.is_set() and (message_limit is None or received < message_limit):
                if connection is None:
                    connection = self.connector.connect(self.url)
                    with self._connection_lock:
                        self._connection = connection
                try:
                    raw = connection.receive()
                    if raw is None or raw == "":
                        raise EOFError("Binance WebSocket closed")
                    self._journal(raw)
                    handler(json.loads(raw) if isinstance(raw, str) else raw)
                    received += 1
                except Exception as error:
                    connection.close()
                    connection = None
                    with self._connection_lock:
                        self._connection = None
                    if self._stop.is_set():
                        break
                    if not isinstance(error, (ConnectionError, EOFError)):
                        raise
                    reconnects += 1
                    if reconnects > self.maximum_reconnects:
                        raise ConnectionError("Binance stream reconnect limit exceeded")
                    if on_reconnect is not None:
                        on_reconnect(reconnects)
        finally:
            if connection is not None:
                connection.close()
            with self._connection_lock:
                self._connection = None
        return received

    def stop(self) -> None:
        self._stop.set()
        with self._connection_lock:
            connection = self._connection
        if connection is not None:
            connection.close()

    def _journal(self, raw: str | dict[str, Any]) -> None:
        if self.journal is None:
            return
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        text = raw if isinstance(raw, str) else json.dumps(
            raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def _decimal(value):
    return Decimal(str(value)) if value not in (None, "") else None


__all__ = [
    "BinanceStreamSession",
    "WebSocketClientConnection",
    "WebSocketClientConnector",
    "WebSocketConnection",
    "WebSocketConnector",
    "parse_market_stream_event",
    "websocket_url",
]
