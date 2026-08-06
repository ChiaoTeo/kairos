from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from kairospy.application.usecases.market.application.integration import MarketFeedSubscriptionRequest
from kairospy.domain.market import Bar, MarketEvent, MarketSubject, Quote, RateObservation, TradePrint
from kairospy.application.usecases.market.application.requests import MarketOptions, MarketTime
from kairospy.application.usecases.reference.application.builders import catalog_from_market_snapshot
from kairospy.application.usecases.reference.application.requests import ReferenceCatalogRequest
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.infrastructure.integrations.domain import AccessScope, ProductFamily, TransportKind
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.domain.reference import InstrumentId, MarketRef, OptionContractRef

from .client import BinanceOptionsRestClient
from .operations import BinanceOptionsMarketOperations
from .stream import BinanceOptionsMarketStream


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
        rows = self._contract_rows(underlying=underlying)
        result: list[OptionContractRef] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("source_symbol") or "")
            base = str(row.get("base") or "").upper()
            expiry = _optional_time(row.get("expiry"))
            strike = _decimal(row.get("strike_price"))
            right = str(row.get("contract_type") or "").lower()
            if not symbol or not base or strike is None or right not in {"call", "put"}:
                continue
            market = MarketRef.ephemeral(venue="binance", market="options", source_symbol=symbol)
            result.append(OptionContractRef(market, InstrumentId(f"instrument:crypto:{base.lower()}"), expiry, strike, right, _decimal(row.get("shares_per_contract")) or Decimal("1")))
        return tuple(result)

    def catalog(self, request: ReferenceCatalogRequest):
        """Translate Binance Options exchangeInfo into the shared catalog."""
        rows = self._contract_rows(underlying=request.underlying)
        return catalog_from_market_snapshot(rows, effective_from=request.as_of)

    def _contract_rows(self, *, underlying: str | None = None) -> tuple[Mapping[str, object], ...]:
        payload = self.operations.exchange_info()
        if not isinstance(payload, Mapping):
            raise ValueError("Binance Options exchangeInfo response must be an object")
        values = payload.get("optionSymbols")
        if values is None:
            values = payload.get("symbols")
        if not isinstance(values, list):
            raise ValueError("Binance Options exchangeInfo response has no optionSymbols list")
        requested = "" if underlying is None else underlying.upper()
        rows: list[Mapping[str, object]] = []
        for value in values:
            if not isinstance(value, Mapping):
                continue
            base = str(value.get("underlying") or value.get("underlyingAsset") or "").upper()
            if requested and base not in {requested, f"{requested}USDT"}:
                continue
            symbol = str(value.get("symbol") or "")
            if not symbol or str(value.get("status") or "TRADING").upper() != "TRADING":
                continue
            base_asset = base[:-4] if base.endswith("USDT") else base
            right = str(value.get("side") or value.get("optionType") or "").lower()
            right = {"c": "call", "p": "put"}.get(right, right)
            rows.append({
                "venue": "binance",
                "market": "options",
                "source_symbol": symbol.upper(),
                "base": base_asset,
                "quote": "USDT",
                "underlying_instrument_id": f"instrument:crypto:{base_asset.lower()}",
                "expiry": _time(value.get("expiryDate") or value.get("expirationTime")),
                "strike_price": value.get("strikePrice") or value.get("strike"),
                "contract_type": right,
                "shares_per_contract": value.get("unit") or value.get("contractSize") or "1",
                "status": "active",
                "raw": value,
            })
        return tuple(rows)

    def bars(self, symbol: str, *, timeframe: str = "1m", since: MarketTime | None = None, until: MarketTime | None = None, limit: int = 1000, adapter_options: MarketOptions | None = None) -> Iterable[Bar]:
        del symbol, timeframe, since, until, limit, adapter_options
        return ()

    def funding_rates(self, symbol: str, *, since: MarketTime | None = None, until: MarketTime | None = None, limit: int = 1000, adapter_options: MarketOptions | None = None) -> Iterable[RateObservation]:
        del symbol, since, until, limit, adapter_options
        return ()


class BinanceOptionsPublicStreamConnection(BinanceOptionsPublicRestConnection):
    def __init__(self, spec: IntegrationConnectionSpec, *, driver=None) -> None:
        self.stream = BinanceOptionsMarketStream(driver=driver) if driver is not None else BinanceOptionsMarketStream()
        self._subscriptions: dict[str, _NativeSubscription] = {}
        super().__init__(spec)
        self.components = (self.stream,)

    async def subscribe(self, request: MarketFeedSubscriptionRequest) -> "_NativeSubscription":
        channel = _stream_channel(request.selector)
        remote_id = await self.stream.subscribe(str(request.market.source_symbol), {channel})
        remote = _NativeSubscription(remote_id, self, request, channel)
        self._subscriptions[remote.subscription_id] = remote
        return remote

    async def unsubscribe(self, subscription_id: str) -> None:
        remote = self._subscriptions.pop(subscription_id, None)
        if remote is not None:
            await remote.close()


@dataclass(slots=True)
class _NativeSubscription:
    subscription_id: str
    connection: BinanceOptionsPublicStreamConnection
    request: MarketFeedSubscriptionRequest
    channel: str

    def events(self) -> AsyncIterator[MarketEvent]:
        return self._events()

    async def close(self) -> None:
        await self.connection.stream.unsubscribe(self.subscription_id)

    async def _events(self) -> AsyncIterator[MarketEvent]:
        async for payload in self.connection.stream.events(self.subscription_id):
            observed_at = _time(payload.get("E") or payload.get("T") or payload.get("t"))
            if self.channel == "bookTicker":
                value = Quote(
                    instrument_id=self.request.market.instrument_id,
                    market_id=self.request.market.market_id,
                    market_key=self.request.market.market_key,
                    time=observed_at,
                    bid=_decimal(payload.get("b")),
                    ask=_decimal(payload.get("a")),
                    bid_size=_decimal(payload.get("B")),
                    ask_size=_decimal(payload.get("A")),
                    source="binance",
                    basis="venue_book",
                )
            elif self.channel == "optionTrade":
                price = _decimal(payload.get("p"))
                size = _decimal(payload.get("q"))
                value = TradePrint(
                    instrument_id=self.request.market.instrument_id,
                    market_id=self.request.market.market_id,
                    market_key=self.request.market.market_key,
                    time=observed_at,
                    trade_id=None if payload.get("t") is None else str(payload.get("t")),
                    price=price,
                    size=size,
                    cost=None if price is None or size is None else price * size,
                    source="binance",
                )
            else:
                continue
            yield MarketEvent(
                subject=MarketSubject("market", self.request.market.market_id),
                observed_at=observed_at,
                available_at=datetime.now(timezone.utc),
                value=value,
                source="binance",
                metadata={"product": "options", "transport": "websocket", "channel": self.channel},
            )


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


def _optional_time(value: object) -> datetime:
    return _time(value)


def _stream_channel(selector: object) -> str:
    model = getattr(selector, "model", selector)
    name = getattr(model, "__name__", str(model)).lower()
    if "quote" in name:
        return "bookTicker"
    if "trade" in name:
        return "optionTrade"
    raise ValueError(f"Binance Options stream does not support selector: {name}")


__all__ = ["BinanceOptionsPublicRestConnection", "BinanceOptionsPublicRestGateway", "BinanceOptionsPublicStreamConnection", "BinanceOptionsPublicStreamGateway"]
