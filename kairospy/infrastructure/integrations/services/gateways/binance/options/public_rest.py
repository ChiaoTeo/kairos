from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from kairospy.application.usecases.market.application.integration import MarketFeedSubscriptionRequest
from kairospy.domain.market import Bar, MarketEvent, MarketSubject, Quote
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.domain.reference import InstrumentId, MarketRef, OptionContractRef

from .client import BinanceOptionsRestClient
from .operations import BinanceOptionsMarketOperations


class BinanceOptionsPublicRestConnection(Connection):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self.operations = BinanceOptionsMarketOperations(BinanceOptionsRestClient())
        super().__init__(spec, components=())

    def latest_quote(self, symbol: str) -> Quote | None:
        payload = self.operations.ticker(symbol=symbol.upper())
        row = payload[0] if isinstance(payload, list) and payload else payload
        if not isinstance(row, Mapping):
            return None
        market = MarketRef.ephemeral(venue="binance", market="options", source_symbol=symbol.upper())
        return Quote(
            instrument_id=market.instrument_id,
            market_id=market.market_id,
            market_key=market.market_key,
            time=_time(row.get("timestamp") or row.get("E")),
            bid=_decimal(row.get("bidPrice") or row.get("b")),
            ask=_decimal(row.get("askPrice") or row.get("a")),
            source="binance",
        )

    def contracts(self, *, underlying: str | None = None) -> tuple[OptionContractRef, ...]:
        payload = self.operations.exchange_info()
        rows = payload.get("optionSymbols", payload.get("symbols", ())) if isinstance(payload, Mapping) else payload
        result: list[OptionContractRef] = []
        for row in rows if isinstance(rows, list) else ():
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "")
            base = str(row.get("underlying") or row.get("underlyingAsset") or "").upper()
            requested_underlying = "" if underlying is None else underlying.upper()
            if requested_underlying and base not in {requested_underlying, f"{requested_underlying}USDT"}:
                continue
            if str(row.get("status") or "TRADING").upper() != "TRADING":
                continue
            expiry = _time(row.get("expiryDate") or row.get("expirationTime"))
            strike = _decimal(row.get("strikePrice") or row.get("strike"))
            right = str(row.get("side") or row.get("optionType") or "").lower()
            right = {"c": "call", "p": "put"}.get(right, right)
            if not symbol or not base or strike is None or right not in {"call", "put"}:
                continue
            base_asset = base[:-4] if base.endswith("USDT") else base
            market = MarketRef.ephemeral(venue="binance", market="options", source_symbol=symbol)
            result.append(OptionContractRef(market, InstrumentId(f"instrument:crypto:{base_asset.lower()}"), expiry, strike, right, _decimal(row.get("unit") or row.get("contractSize")) or Decimal("1")))
        return tuple(result)

    def bars(self, symbol: str, *, timeframe: str = "1m", since: object | None = None, until: object | None = None, limit: int = 1000, adapter_options: Mapping[str, object] | None = None) -> Iterable[Bar]:
        del symbol, timeframe, since, until, limit, adapter_options
        return ()

    def funding_rates(self, symbol: str, *, since: object | None = None, until: object | None = None, limit: int = 1000, adapter_options: Mapping[str, object] | None = None) -> Iterable[object]:
        del symbol, since, until, limit, adapter_options
        return ()


class BinanceOptionsPublicStreamConnection(BinanceOptionsPublicRestConnection):
    def __init__(self, spec: IntegrationConnectionSpec) -> None:
        self._subscriptions: dict[str, _PollingSubscription] = {}
        super().__init__(spec)

    async def subscribe(self, request: MarketFeedSubscriptionRequest) -> "_PollingSubscription":
        remote = _PollingSubscription(f"binance-options-{uuid4().hex}", self, request, _poll_seconds(request.params))
        self._subscriptions[remote.subscription_id] = remote
        return remote

    async def unsubscribe(self, subscription_id: str) -> None:
        remote = self._subscriptions.pop(subscription_id, None)
        if remote is not None:
            await remote.close()


@dataclass(slots=True)
class _PollingSubscription:
    subscription_id: str
    connection: BinanceOptionsPublicRestConnection
    request: MarketFeedSubscriptionRequest
    poll_seconds: float
    closed: asyncio.Event = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.closed = asyncio.Event()

    def events(self) -> AsyncIterator[MarketEvent]:
        return self._events()

    async def close(self) -> None:
        self.closed.set()

    async def _events(self) -> AsyncIterator[MarketEvent]:
        while not self.closed.is_set():
            value = await asyncio.to_thread(self.connection.latest_quote, str(self.request.market.source_symbol))
            if value is not None:
                yield MarketEvent(
                    subject=MarketSubject("market", self.request.market.market_id),
                    observed_at=value.time,
                    available_at=datetime.now(timezone.utc),
                    value=value,
                    source="binance",
                    metadata={"product": "options", "transport": "rest_polling"},
                )
            try:
                await asyncio.wait_for(self.closed.wait(), timeout=self.poll_seconds)
            except asyncio.TimeoutError:
                pass


class BinanceOptionsPublicRestGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceOptionsPublicRestConnection:
        _validate(spec, TransportKind.REST)
        return BinanceOptionsPublicRestConnection(spec)


class BinanceOptionsPublicStreamGateway:
    def open(self, spec: IntegrationConnectionSpec) -> BinanceOptionsPublicStreamConnection:
        _validate(spec, TransportKind.MARKET_STREAM)
        return BinanceOptionsPublicStreamConnection(spec)


def _validate(spec: IntegrationConnectionSpec, transport: TransportKind) -> None:
    if spec.product is not ProductFamily.OPTIONS or spec.access is not AccessScope.PUBLIC or spec.transport is not transport:
        raise ValueError("Binance Options public gateway received an incompatible connection spec")


def _decimal(value: object) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (TypeError, ValueError):
        return None


def _time(value: object) -> datetime:
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def _poll_seconds(params: Mapping[str, object]) -> float:
    try:
        return max(float(params.get("poll_seconds", 1.0)), 0.05)
    except (TypeError, ValueError):
        return 1.0


__all__ = ["BinanceOptionsPublicRestConnection", "BinanceOptionsPublicRestGateway", "BinanceOptionsPublicStreamConnection", "BinanceOptionsPublicStreamGateway"]
