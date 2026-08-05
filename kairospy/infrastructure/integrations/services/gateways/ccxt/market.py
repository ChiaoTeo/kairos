"""CCXT-backed market connections.

CCXT is used for the common crypto market surface. The CCXT exchange object
never leaves this module: callers receive the typed Market application port
and canonical kairospy market domain values.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from kairospy.domain.market import Bar, MarketEvent, MarketSubject, Quote, TradePrint
from kairospy.infrastructure.integrations.application.connections import IntegrationConnectionSpec
from kairospy.application.usecases.market.application.integration import MarketFeedSubscriptionRequest
from kairospy.infrastructure.integrations.domain import ProductFamily
from kairospy.infrastructure.integrations.services.connections.connection import Connection
from kairospy.infrastructure.integrations.services.credentials import credential_value
from kairospy.infrastructure.integrations.services.drivers.http import HttpDriver


class CcxtMarketConnection(Connection):
    """Typed Market data and stream ports backed by CCXT.

    The stream implementation is deliberately polling-based.  It provides a
    deterministic common fallback for exchanges whose native websocket shape
    differs; a native adapter can replace it without changing the Market port.
    """

    def __init__(
        self,
        spec: IntegrationConnectionSpec,
        *,
        exchange: object | None = None,
    ) -> None:
        self._exchange = exchange
        self._exchange_id = _exchange_id(spec)
        self._market_type = _ccxt_market_type(spec.product, self._exchange_id)
        self._subscriptions: dict[str, CcxtMarketSubscription] = {}
        self._native_hyperliquid = None
        super().__init__(spec, components=())

    def latest_quote(self, symbol: str) -> Quote | None:
        try:
            ticker = self._client().fetch_ticker(self._symbol(symbol))
        except Exception:
            if self._exchange_id != "hyperliquid":
                raise
            return self._hyperliquid_quote(symbol)
        if not isinstance(ticker, Mapping):
            raise TypeError("CCXT ticker response must be a mapping")
        return _quote(ticker, symbol=symbol, market_type=str(self.spec.product or "spot"), source=self._exchange_id)

    def bars(
        self,
        symbol: str,
        *,
        timeframe: str = "1m",
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 1000,
        adapter_options: Mapping[str, object] | None = None,
    ) -> Iterable[Bar]:
        kwargs = {
            "timeframe": timeframe,
            "since": None if since is None else int(since.timestamp() * 1000),
            "limit": limit,
        }
        if adapter_options:
            kwargs["params"] = dict(adapter_options)
        rows = self._client().fetch_ohlcv(self._symbol(symbol), **kwargs)
        for row in rows or ():
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            observed_at = datetime.fromtimestamp(float(row[0]) / 1000, tz=timezone.utc)
            if until is not None and observed_at > until:
                continue
            yield Bar(
                instrument_id=_instrument_id(self._symbol(symbol)),
                market_id=_market_id(self.spec, symbol),
                market_key=_market_key(self.spec, symbol),
                time=observed_at,
                timeframe=timeframe,
                open=_decimal(row[1]),
                high=_decimal(row[2]),
                low=_decimal(row[3]),
                close=_decimal(row[4]),
                volume=_decimal(row[5]),
                source=self._exchange_id,
            )

    def funding_rates(
        self,
        symbol: str,
        *,
        since: object | None = None,
        until: object | None = None,
        limit: int = 1000,
        adapter_options: Mapping[str, object] | None = None,
    ) -> Iterable[object]:
        client = self._client()
        method = getattr(client, "fetch_funding_rate_history", None)
        if not callable(method):
            current = client.fetch_funding_rate(self._symbol(symbol))
            return () if current is None else (current,)
        rows = method(
            self._symbol(symbol),
            since=_millis(since),
            limit=limit,
            params=dict(adapter_options or {}),
        )
        if until is None:
            return rows or ()
        until_ms = _millis(until)
        return tuple(row for row in rows or () if isinstance(row, Mapping) and (row.get("timestamp") is None or int(row["timestamp"]) <= int(until_ms)))

    async def subscribe(self, request: MarketFeedSubscriptionRequest) -> "CcxtMarketSubscription":
        subscription = CcxtMarketSubscription(
            subscription_id=f"ccxt-{self._exchange_id}-{uuid4().hex}",
            connection=self,
            request=request,
            poll_seconds=_poll_seconds(request.params),
        )
        self._subscriptions[subscription.subscription_id] = subscription
        return subscription

    async def unsubscribe(self, subscription_id: str) -> None:
        subscription = self._subscriptions.pop(subscription_id, None)
        if subscription is not None:
            await subscription.close()

    def _client(self) -> Any:
        if self._exchange is None:
            try:
                import ccxt  # type: ignore[import-not-found]
            except ImportError as error:
                raise RuntimeError("CCXT market support requires the crypto extra") from error
            exchange_type = getattr(ccxt, self._exchange_id, None)
            if exchange_type is None:
                raise ValueError(f"unsupported CCXT exchange: {self._exchange_id}")
            config: dict[str, object] = {
                "enableRateLimit": True,
                "options": {"defaultType": self._market_type},
            }
            credential = self.spec.credential.id if self.spec.credential else None
            api_key = credential_value(credential, "API_KEY")
            secret = credential_value(credential, "SECRET")
            password = credential_value(credential, "PASSPHRASE") or credential_value(credential, "PASSWORD")
            if api_key:
                config["apiKey"] = api_key
            if secret:
                config["secret"] = secret
            if password:
                config["password"] = password
            self._exchange = exchange_type(config)
        return self._exchange

    def _hyperliquid_quote(self, symbol: str) -> Quote | None:
        if self._native_hyperliquid is None:
            self._native_hyperliquid = _HyperliquidMarketClient()
        return self._native_hyperliquid.quote(
            symbol,
            market_type=str(self.spec.product or "spot"),
        )

    def _symbol(self, symbol: str) -> str:
        value = str(symbol).strip().upper().replace("-SWAP", "")
        if ":" in value:
            return value
        if "/" in value:
            if self._market_type in {"swap", "future"} and value.count("/") == 1:
                return f"{value}:USDT"
            return value
        for quote in ("USDT", "USDC", "USD", "BTC", "ETH"):
            if value.endswith(quote) and len(value) > len(quote):
                base = value[: -len(quote)]
                pair = f"{base}/{quote}"
                return f"{pair}:USDT" if self._market_type in {"swap", "future"} else pair
        return value

    def _poll(self, request: MarketFeedSubscriptionRequest) -> MarketEvent:
        symbol = str(request.market.source_symbol)
        selector = request.selector.model
        if selector is Quote:
            value = self.latest_quote(symbol)
        elif selector is Bar:
            timeframe = request.selector.interval or str(request.params.get("timeframe", "1m"))
            value = next(iter(self.bars(symbol, timeframe=timeframe, limit=1)), None)
        elif selector is TradePrint:
            trades = self._client().fetch_trades(self._symbol(symbol), limit=1)
            value = _trade(next(iter(trades or ()), None), symbol=symbol, market_type=str(self.spec.product or "spot"), source=self._exchange_id)
        else:
            raise ValueError(f"CCXT polling does not support selector: {getattr(selector, '__name__', selector)}")
        if value is None:
            raise RuntimeError(f"CCXT returned no market value for {symbol}")
        observed_at = getattr(value, "time", datetime.now(timezone.utc))
        return MarketEvent(
            subject=MarketSubject("market", request.market.market_id),
            observed_at=observed_at,
            available_at=datetime.now(timezone.utc),
            value=value,
            source=self._exchange_id,
            metadata={"product": str(self.spec.product or "spot"), "transport": "ccxt_polling"},
        )


class CcxtMarketSubscription:
    def __init__(self, *, subscription_id: str, connection: CcxtMarketConnection, request: MarketFeedSubscriptionRequest, poll_seconds: float) -> None:
        self.subscription_id = subscription_id
        self._connection = connection
        self._request = request
        self._poll_seconds = poll_seconds
        self._closed = asyncio.Event()

    def events(self):
        return self._events()

    async def close(self) -> None:
        self._closed.set()

    async def _events(self):
        while not self._closed.is_set():
            event = await asyncio.to_thread(self._connection._poll, self._request)
            yield event
            try:
                await asyncio.wait_for(self._closed.wait(), timeout=self._poll_seconds)
            except asyncio.TimeoutError:
                pass


class _HyperliquidMarketClient:
    def __init__(self) -> None:
        self.driver = HttpDriver(timeout_seconds=10)

    def quote(self, symbol: str, *, market_type: str) -> Quote | None:
        coin = str(symbol).split("/", 1)[0].split(":", 1)[0].upper()
        payload = self._post({"type": "l2Book", "coin": coin})
        if isinstance(payload, Mapping):
            levels = payload.get("levels")
            if isinstance(levels, (list, tuple)) and len(levels) >= 2:
                bids = levels[0] if isinstance(levels[0], (list, tuple)) else ()
                asks = levels[1] if isinstance(levels[1], (list, tuple)) else ()
                bid = _book_price(bids[0] if bids else None)
                ask = _book_price(asks[0] if asks else None)
                if bid is not None or ask is not None:
                    return _quote(
                        {"bid": bid, "ask": ask, "timestamp": payload.get("time")},
                        symbol=symbol,
                        market_type=market_type,
                        source="hyperliquid",
                    )
        mids = self._post({"type": "allMids"})
        if isinstance(mids, Mapping):
            mid = mids.get(coin)
            if mid is not None:
                return _quote(
                    {"bid": mid, "ask": mid},
                    symbol=symbol,
                    market_type=market_type,
                    source="hyperliquid",
                )
        return None

    def _post(self, payload: Mapping[str, object]) -> object:
        # HttpDriver intentionally exposes query parameters only.  Use the
        # session directly for Hyperliquid's JSON body while keeping the
        # timeout and HTTP boundary in infrastructure.
        session = self.driver.session
        if session is None:
            import requests

            session = requests.Session()
        response = session.post(
            "https://api.hyperliquid.xyz/info",
            json=dict(payload),
            timeout=self.driver.timeout_seconds,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()


def _exchange_id(spec: IntegrationConnectionSpec) -> str:
    for participant in spec.participants:
        if participant.kind.value == "exchange":
            value = str(participant.id)
            return "okx" if value == "okex" else value
    for participant in spec.participants:
        if participant.kind.value == "provider":
            value = str(participant.id)
            return "okx" if value == "okex" else value
    raise ValueError("CCXT market connection requires an exchange or provider participant")


def _ccxt_market_type(product: ProductFamily | None, exchange_id: str) -> str:
    if product in {ProductFamily.USD_M_FUTURES, ProductFamily.COIN_M_FUTURES}:
        return "future" if exchange_id == "binance" else "swap"
    return "spot"


def _quote(payload: Mapping[str, object], *, symbol: str, market_type: str, source: str) -> Quote | None:
    time_value = payload.get("timestamp") or payload.get("datetime")
    observed_at = _timestamp(time_value) or datetime.now(timezone.utc)
    info = payload.get("info")
    info_values = info if isinstance(info, Mapping) else {}
    bid = payload.get("bid") or info_values.get("bidPrice") or info_values.get("bidPx")
    ask = payload.get("ask") or info_values.get("askPrice") or info_values.get("askPx")
    if bid is None and ask is None:
        bid = ask = payload.get("last") or payload.get("close") or info_values.get("last")
    return Quote(
        instrument_id=_instrument_id(symbol),
        market_id=None,
        market_key=None,
        time=observed_at,
        bid=_decimal(bid),
        ask=_decimal(ask),
        bid_size=_decimal(payload.get("bidVolume") or info_values.get("bidSz")),
        ask_size=_decimal(payload.get("askVolume") or info_values.get("askSz")),
        source=source,
        basis=market_type,
    )


def _trade(payload: object, *, symbol: str, market_type: str, source: str) -> TradePrint | None:
    if not isinstance(payload, Mapping):
        return None
    observed_at = _timestamp(payload.get("timestamp")) or datetime.now(timezone.utc)
    return TradePrint(
        instrument_id=_instrument_id(symbol),
        time=observed_at,
        trade_id=None if payload.get("id") is None else str(payload.get("id")),
        side=None if payload.get("side") is None else str(payload.get("side")),
        price=_decimal(payload.get("price")),
        size=_decimal(payload.get("amount")),
        cost=_decimal(payload.get("cost")),
        source=source,
        basis=market_type,
    )


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 10_000_000_000:
        number /= 1000
    return datetime.fromtimestamp(number, tz=timezone.utc)


def _millis(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"timestamp must be datetime or integer milliseconds: {value!r}") from error


def _decimal(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _book_price(value: object) -> object | None:
    if isinstance(value, Mapping):
        return value.get("px") or value.get("price")
    return None


def _poll_seconds(params: Mapping[str, object]) -> float:
    try:
        value = float(params.get("poll_seconds", 1.0))
    except (TypeError, ValueError) as error:
        raise ValueError("poll_seconds must be a positive number") from error
    if value <= 0:
        raise ValueError("poll_seconds must be a positive number")
    return value


def _instrument_id(symbol: str) -> str:
    return f"instrument:{str(symbol).strip().lower().replace('/', ':')}"


def _market_id(spec: IntegrationConnectionSpec, symbol: str) -> str:
    return f"market:{_exchange_id(spec)}:{str(spec.product or 'spot').lower()}:{symbol.lower()}"


def _market_key(spec: IntegrationConnectionSpec, symbol: str) -> str:
    return f"{_exchange_id(spec)}_{str(spec.product or 'spot').lower()}_{symbol.lower().replace('/', '_')}"


class CcxtMarketGateway:
    def open(self, spec: IntegrationConnectionSpec) -> CcxtMarketConnection:
        return CcxtMarketConnection(spec)


__all__ = ["CcxtMarketConnection", "CcxtMarketGateway", "CcxtMarketSubscription"]
