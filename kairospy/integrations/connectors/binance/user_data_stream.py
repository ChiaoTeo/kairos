from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from contextlib import suppress

from kairospy.environment import Environment
from kairospy.identity import AccountRef, AssetId, InstrumentId
from kairospy.market.stream import BoundedEventChannel

from .market_stream import BinanceStreamSession, WebSocketClientConnector, WebSocketConnector, websocket_url
from .rest_transport import BinanceTransport, RateLimiter


@dataclass(frozen=True, slots=True)
class UserFillUpdate:
    execution_id: str
    order_id: str
    client_order_id: str
    account: AccountRef
    instrument_id: InstrumentId
    side: str
    quantity: Decimal
    price: Decimal
    commission: Decimal
    commission_asset: AssetId
    event_time: datetime
    fully_filled: bool = False


@dataclass(frozen=True, slots=True)
class BalanceUpdate:
    balances: tuple[tuple[AssetId, Decimal, Decimal], ...]
    event_time: datetime


class BinanceUserDataStreamService:
    """Creates and maintains listen keys without exposing withdrawal capabilities."""

    def __init__(self, transport: BinanceTransport, api_key: str, *, futures: bool = False, inverse: bool = False, options: bool = False, limiter: RateLimiter | None = None) -> None:
        if options and (futures or inverse) or inverse and not futures:
            raise ValueError("invalid Binance user stream market selection")
        self.transport, self.api_key = transport, api_key
        self.futures, self.inverse, self.options = futures, inverse, options
        self.limiter = limiter or RateLimiter(1200, 60)

    @property
    def path(self) -> str:
        if self.options:
            return "/eapi/v1/listenKey"
        if self.inverse:
            return "/dapi/v1/listenKey"
        if self.futures:
            return "/fapi/v1/listenKey"
        return "/api/v3/userDataStream"

    def create(self) -> str:
        self.limiter.acquire()
        row = self.transport.request("POST", self.path, headers={"X-MBX-APIKEY": self.api_key})
        return str(row["listenKey"])

    def keepalive(self, listen_key: str) -> None:
        self.limiter.acquire()
        self.transport.request("PUT", self.path, {"listenKey": listen_key}, {"X-MBX-APIKEY": self.api_key})

    def close(self, listen_key: str) -> None:
        self.limiter.acquire()
        self.transport.request("DELETE", self.path, {"listenKey": listen_key}, {"X-MBX-APIKEY": self.api_key})


def parse_user_stream_event(row: dict, account, instrument_lookup: dict[str, InstrumentId]):
    event_type = row.get("e")
    if event_type == "executionReport" and row.get("x") == "TRADE":
        symbol = row["s"]
        return UserFillUpdate(
            str(row["t"]), str(row["i"]), str(row.get("c") or row["i"]),
            account, instrument_lookup[symbol], row["S"].lower(),
            Decimal(row["l"]), Decimal(row["L"]), Decimal(row["n"]), AssetId(row["N"]),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
            str(row.get("X") or "").upper() == "FILLED",
        )
    if event_type == "outboundAccountPosition":
        return BalanceUpdate(
            tuple((AssetId(item["a"]), Decimal(item["f"]), Decimal(item["l"])) for item in row["B"]),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    if event_type == "ORDER_TRADE_UPDATE" and isinstance(row.get("o"), (list, tuple)):
        fills: list[UserFillUpdate] = []
        for order in row["o"]:
            if not isinstance(order, dict):
                continue
            symbol = order["s"]
            for fill in order.get("fi") or ():
                fills.append(UserFillUpdate(
                    str(fill["t"]), str(order.get("oid") or order.get("i") or fill["t"]),
                    str(order.get("c") or order.get("oid") or order.get("i") or fill["t"]),
                    account, instrument_lookup[symbol], str(order["S"]).lower(),
                    Decimal(fill["q"]), Decimal(fill["p"]), Decimal(fill.get("f", "0")),
                    AssetId(fill.get("a") or order.get("ma") or "USDT"),
                    datetime.fromtimestamp(int(fill.get("T") or order.get("T") or row["E"]) / 1000, timezone.utc),
                    str(order.get("X") or "").upper() == "FILLED",
                ))
        return tuple(fills) or None
    order_payload = row.get("o")
    if event_type == "ORDER_TRADE_UPDATE" and isinstance(order_payload, dict) and order_payload.get("x") == "TRADE":
        order = order_payload
        symbol = order["s"]
        return UserFillUpdate(
            str(order["t"]), str(order["i"]), str(order.get("c") or order["i"]),
            account, instrument_lookup[symbol], order["S"].lower(),
            Decimal(order["l"]), Decimal(order["L"]), Decimal(order.get("n", "0")),
            AssetId(order.get("N") or order.get("ma") or "USDT"),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
            str(order.get("X") or "").upper() == "FILLED",
        )
    if event_type == "ACCOUNT_UPDATE":
        balances = row.get("a", {}).get("B", [])
        return BalanceUpdate(
            tuple((AssetId(item["a"]), Decimal(item["wb"]), Decimal("0")) for item in balances),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    return None


class BinanceUserStreamProcessor:
    def __init__(self, account: AccountRef, instrument_lookup: dict[str, InstrumentId]) -> None:
        self.account, self.instrument_lookup = account, instrument_lookup
        self._execution_ids: set[str] = set()

    def process(self, row: dict):
        event = parse_user_stream_event(row, self.account, self.instrument_lookup)
        if isinstance(event, UserFillUpdate):
            if event.execution_id in self._execution_ids:
                return None
            self._execution_ids.add(event.execution_id)
        if isinstance(event, tuple):
            unique = []
            for item in event:
                if not isinstance(item, UserFillUpdate):
                    continue
                if item.execution_id in self._execution_ids:
                    continue
                self._execution_ids.add(item.execution_id)
                unique.append(item)
            return tuple(unique) or None
        return event


class BinanceUserFillEventSource:
    """Live Binance user stream source that yields durable fill updates."""

    def __init__(
        self,
        stream_service: BinanceUserDataStreamService,
        processor: BinanceUserStreamProcessor,
        *,
        environment: Environment,
        connector: WebSocketConnector | None = None,
        futures: bool = False,
        inverse: bool = False,
        options: bool = False,
        keepalive_seconds: float = 30 * 60,
        channel_capacity: int = 128,
        maximum_reconnects: int = 5,
        message_limit: int | None = None,
    ) -> None:
        if keepalive_seconds <= 0:
            raise ValueError("Binance user stream keepalive interval must be positive")
        self.stream_service = stream_service
        self.processor = processor
        self.environment = Environment(environment)
        self.connector = connector or WebSocketClientConnector()
        self.futures = futures
        self.inverse = inverse
        self.options = options
        self.keepalive_seconds = keepalive_seconds
        self.channel_capacity = channel_capacity
        self.maximum_reconnects = maximum_reconnects
        self.message_limit = message_limit
        self.listen_key: str | None = None

    async def events(self):
        listen_key = await asyncio.to_thread(self.stream_service.create)
        self.listen_key = listen_key
        session = BinanceStreamSession(
            self.connector,
            websocket_url(self.environment, listen_key, futures=self.futures, options=self.options, public_only=False),
            maximum_reconnects=self.maximum_reconnects,
        )
        channel = BoundedEventChannel(self.channel_capacity)
        loop = asyncio.get_running_loop()

        def consume(row: dict[str, object]) -> None:
            event = self.processor.process(row)
            if isinstance(event, UserFillUpdate):
                asyncio.run_coroutine_threadsafe(channel.publish(event), loop).result()
            elif isinstance(event, tuple):
                for item in event:
                    if isinstance(item, UserFillUpdate):
                        asyncio.run_coroutine_threadsafe(channel.publish(item), loop).result()

        async def keepalive() -> None:
            while True:
                await asyncio.sleep(self.keepalive_seconds)
                await asyncio.to_thread(self.stream_service.keepalive, listen_key)

        worker = asyncio.create_task(asyncio.to_thread(
            session.consume,
            consume,
            message_limit=self.message_limit,
        ))
        keepalive_task = asyncio.create_task(keepalive())
        closer = asyncio.create_task(_close_channel_when_done(worker, channel))
        try:
            async for event in channel.events():
                yield event
            if worker.done():
                error = worker.exception()
                if error is not None:
                    raise error
        finally:
            session.stop()
            keepalive_task.cancel()
            closer.cancel()
            with suppress(asyncio.CancelledError):
                await keepalive_task
            await asyncio.gather(worker, return_exceptions=True)
            await asyncio.to_thread(self.stream_service.close, listen_key)


async def _close_channel_when_done(worker: asyncio.Task, channel: BoundedEventChannel) -> None:
    try:
        await worker
    finally:
        await channel.close()
