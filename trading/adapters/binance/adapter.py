from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from time import monotonic, sleep, time
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from trading.adapters.base import AccountState, Environment, OrderAck, OrderRequest, ReferenceDataRequest, VenueBalance
from trading.domain.capability import (
    ExecutionCapabilities, MarketDataCapabilities, MarketDataKind, MarginMode,
    OrderType, PositionMode, ReferenceCapabilities,
)
from trading.domain.execution import FundingPayment
from trading.domain.identity import AccountKey, AssetId, InstrumentId, VenueId
from trading.domain.instrument import InstrumentDefinition, VenueListing
from trading.domain.market_data import (
    DerivativeMarketState, OrderBookDelta, OrderBookLevel, Quote, Trade,
)
from trading.domain.product import (
    ContractType, CryptoOptionSpec, CryptoSpotSpec, ExerciseStyle, OptionRight,
    FutureSpec, PerpetualSpec, ProductType,
)


BINANCE_SPOT_REFERENCE_CAPABILITIES = ReferenceCapabilities(
    frozenset({ProductType.CRYPTO_SPOT}),
)
BINANCE_FUTURES_REFERENCE_CAPABILITIES = ReferenceCapabilities(
    frozenset({ProductType.PERPETUAL, ProductType.FUTURE}),
)
BINANCE_OPTIONS_REFERENCE_CAPABILITIES = ReferenceCapabilities(
    frozenset({ProductType.CRYPTO_OPTION}),
)

BINANCE_SPOT_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({MarketDataKind.QUOTE, MarketDataKind.TRADE, MarketDataKind.BAR, MarketDataKind.ORDER_BOOK}),
    product_types=frozenset({ProductType.CRYPTO_SPOT}),
)
BINANCE_FUTURES_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({MarketDataKind.QUOTE, MarketDataKind.TRADE, MarketDataKind.BAR, MarketDataKind.ORDER_BOOK, MarketDataKind.INDEX_PRICE, MarketDataKind.MARK_PRICE, MarketDataKind.FUNDING_RATE, MarketDataKind.OPEN_INTEREST}),
    product_types=frozenset({ProductType.PERPETUAL, ProductType.FUTURE}),
)
BINANCE_OPTIONS_MARKET_DATA_CAPABILITIES = MarketDataCapabilities(
    frozenset({MarketDataKind.QUOTE, MarketDataKind.TRADE, MarketDataKind.ORDER_BOOK, MarketDataKind.GREEKS, MarketDataKind.INDEX_PRICE}),
    product_types=frozenset({ProductType.CRYPTO_OPTION}),
    supports_native_greeks=True,
)

BINANCE_SPOT_EXECUTION_CAPABILITIES = ExecutionCapabilities(
    frozenset({OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT}),
    product_types=frozenset({ProductType.CRYPTO_SPOT}),
    supports_post_only=True,
)
BINANCE_FUTURES_EXECUTION_CAPABILITIES = ExecutionCapabilities(
    frozenset({OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT}),
    product_types=frozenset({ProductType.PERPETUAL, ProductType.FUTURE}),
    supports_reduce_only=True,
    supports_post_only=True,
    margin_modes=frozenset({MarginMode.CROSS, MarginMode.ISOLATED}),
    position_modes=frozenset({PositionMode.ONE_WAY, PositionMode.HEDGE}),
)
BINANCE_OPTIONS_EXECUTION_CAPABILITIES = ExecutionCapabilities(
    frozenset({OrderType.LIMIT}),
    product_types=frozenset({ProductType.CRYPTO_OPTION}),
    supports_reduce_only=True,
    supports_post_only=True,
)

_BINANCE_MARKET_DATA_CAPABILITIES = {
    ProductType.CRYPTO_SPOT: BINANCE_SPOT_MARKET_DATA_CAPABILITIES,
    ProductType.PERPETUAL: BINANCE_FUTURES_MARKET_DATA_CAPABILITIES,
    ProductType.FUTURE: BINANCE_FUTURES_MARKET_DATA_CAPABILITIES,
    ProductType.CRYPTO_OPTION: BINANCE_OPTIONS_MARKET_DATA_CAPABILITIES,
}


@dataclass(frozen=True, slots=True)
class UserFillUpdate:
    execution_id: str
    order_id: str
    account: AccountKey
    instrument_id: InstrumentId
    side: str
    quantity: Decimal
    price: Decimal
    commission: Decimal
    commission_asset: AssetId
    event_time: datetime


@dataclass(frozen=True, slots=True)
class BalanceUpdate:
    balances: tuple[tuple[AssetId, Decimal, Decimal], ...]
    event_time: datetime


@dataclass(frozen=True, slots=True)
class OptionMarketSnapshot:
    instrument_id: InstrumentId
    bid: Decimal | None
    ask: Decimal | None
    mark_price: Decimal | None
    index_price: Decimal | None
    implied_volatility: Decimal | None
    delta: Decimal | None
    gamma: Decimal | None
    theta: Decimal | None
    vega: Decimal | None
    event_time: datetime


@dataclass(frozen=True, slots=True)
class RecoverySnapshot:
    open_order_ids: tuple[str, ...]
    fills: tuple[UserFillUpdate, ...]
    account_state: AccountState


class BinanceTransport(Protocol):
    def request(self, method: str, path: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> Any: ...


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


class UrllibBinanceTransport:
    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def request(self, method, path, params=None, headers=None):
        query = urlencode(params or {}, doseq=True)
        url = f"{self.base_url}{path}"
        data = None
        if method.upper() in {"GET", "DELETE"} and query:
            url = f"{url}?{query}"
        elif query:
            data = query.encode()
        request = Request(url, data=data, headers=headers or {}, method=method.upper())
        with urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read())


class RateLimiter:
    def __init__(self, calls: int, period_seconds: float) -> None:
        self.calls, self.period = calls, period_seconds
        self._timestamps = []

    def acquire(self) -> None:
        now = monotonic()
        self._timestamps = [value for value in self._timestamps if now - value < self.period]
        if len(self._timestamps) >= self.calls:
            delay = self.period - (now - self._timestamps[0])
            if delay > 0: sleep(delay)
        self._timestamps.append(monotonic())


class BinanceSigner:
    def __init__(self, api_key: str, secret: str, clock_offset_ms: int = 0) -> None:
        self.api_key, self.secret, self.clock_offset_ms = api_key, secret.encode(), clock_offset_ms

    def signed(self, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, str]]:
        values = dict(params or {})
        values.setdefault("timestamp", int(time() * 1000) + self.clock_offset_ms)
        values.setdefault("recvWindow", 5000)
        query = urlencode(values, doseq=True)
        values["signature"] = hmac.new(self.secret, query.encode(), hashlib.sha256).hexdigest()
        return values, {"X-MBX-APIKEY": self.api_key}

    def synchronize(self, server_time_ms: int, local_time_ms: int | None = None) -> int:
        local = int(time() * 1000) if local_time_ms is None else local_time_ms
        self.clock_offset_ms = server_time_ms - local
        return self.clock_offset_ms


class BinanceSpotReferenceAdapter:
    venue_id = VenueId("binance")
    capabilities = BINANCE_SPOT_REFERENCE_CAPABILITIES

    def __init__(self, transport: BinanceTransport, limiter: RateLimiter | None = None) -> None:
        self.transport, self.limiter = transport, limiter or RateLimiter(1200, 60)

    def sync(self, request: ReferenceDataRequest) -> tuple[InstrumentDefinition, ...]:
        if request.product_type is not ProductType.CRYPTO_SPOT:
            raise ValueError("Binance spot reference adapter requires crypto_spot request")
        symbols = request.symbols
        self.limiter.acquire()
        data = self.transport.request("GET", "/api/v3/exchangeInfo")
        wanted = set(symbols)
        return tuple(_spot_definition(item) for item in data["symbols"] if item["symbol"] in wanted and item.get("status") == "TRADING")


class BinanceFuturesReferenceAdapter:
    venue_id = VenueId("binance")
    capabilities = BINANCE_FUTURES_REFERENCE_CAPABILITIES

    def __init__(self, transport: BinanceTransport, *, inverse: bool = False, limiter: RateLimiter | None = None) -> None:
        self.transport, self.inverse, self.limiter = transport, inverse, limiter or RateLimiter(1200, 60)

    def sync(self, request: ReferenceDataRequest) -> tuple[InstrumentDefinition, ...]:
        if request.product_type not in {ProductType.PERPETUAL, ProductType.FUTURE}:
            raise ValueError("Binance futures reference adapter requires future/perpetual request")
        symbols = request.symbols
        path = "/dapi/v1/exchangeInfo" if self.inverse else "/fapi/v1/exchangeInfo"
        self.limiter.acquire()
        data = self.transport.request("GET", path)
        wanted = set(symbols)
        rows = [item for item in data["symbols"] if item["symbol"] in wanted]
        if request.product_type is ProductType.PERPETUAL:
            return tuple(_perpetual_definition(item, inverse=self.inverse) for item in rows if item.get("contractType") == "PERPETUAL")
        return tuple(_future_definition(item, inverse=self.inverse) for item in rows if item.get("contractType") != "PERPETUAL")


class BinanceOptionsReferenceAdapter:
    venue_id = VenueId("binance")
    capabilities = BINANCE_OPTIONS_REFERENCE_CAPABILITIES

    def __init__(self, transport: BinanceTransport, limiter: RateLimiter | None = None) -> None:
        self.transport, self.limiter = transport, limiter or RateLimiter(1200, 60)

    def sync(self, request: ReferenceDataRequest) -> tuple[InstrumentDefinition, ...]:
        if request.product_type is not ProductType.CRYPTO_OPTION:
            raise ValueError("Binance options reference adapter requires crypto_option request")
        symbols = request.symbols
        self.limiter.acquire()
        data = self.transport.request("GET", "/eapi/v1/exchangeInfo")
        wanted = set(symbols)
        return tuple(_option_definition(item) for item in data.get("optionSymbols", []) if item["symbol"] in wanted)


class BinanceMarketDataAdapter:
    venue_id = VenueId("binance")

    def __init__(self, transport: BinanceTransport, product_type: ProductType = ProductType.CRYPTO_SPOT, path: str | None = None, limiter: RateLimiter | None = None) -> None:
        try:
            self.capabilities = _BINANCE_MARKET_DATA_CAPABILITIES[product_type]
        except KeyError as error:
            raise ValueError(f"Binance market data does not support {product_type}") from error
        default_path = {
            ProductType.CRYPTO_SPOT: "/api/v3/ticker/bookTicker",
            ProductType.PERPETUAL: "/fapi/v1/ticker/bookTicker",
            ProductType.FUTURE: "/fapi/v1/ticker/bookTicker",
            ProductType.CRYPTO_OPTION: "/eapi/v1/ticker",
        }[product_type]
        self.transport, self.product_type, self.path = transport, product_type, path or default_path
        self.limiter = limiter or RateLimiter(1200, 60)

    def snapshot(self, instruments: tuple[InstrumentDefinition, ...]) -> tuple[Quote, ...]:
        self.limiter.acquire()
        data = self.transport.request("GET", self.path)
        rows = data if isinstance(data, list) else [data]
        by_symbol = {item["symbol"]: item for item in rows}
        now = datetime.now(timezone.utc)
        result = []
        for definition in instruments:
            self.capabilities.require_product(definition.product_type)
            listing = definition.listing(self.venue_id)
            row = by_symbol[listing.symbol]
            result.append(Quote(definition.instrument_id, _decimal(row.get("bidPrice")), _decimal(row.get("askPrice")), _decimal(row.get("bidQty")), _decimal(row.get("askQty")), now))
        return tuple(result)


class BinanceExecutionAdapter:
    venue_id = VenueId("binance")

    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, environment: Environment, *, futures: bool = False, inverse: bool = False, limiter: RateLimiter | None = None, instrument_symbols: dict[InstrumentId, str] | None = None) -> None:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise ValueError("Binance execution requires testnet or live")
        if inverse and not futures:
            raise ValueError("inverse execution requires futures=True")
        self.transport, self.signer, self.environment, self.futures, self.inverse = transport, signer, environment, futures, inverse
        self.limiter = limiter or RateLimiter(1200, 60)
        self.instrument_symbols = dict(instrument_symbols or {})
        self.capabilities = BINANCE_FUTURES_EXECUTION_CAPABILITIES if futures else BINANCE_SPOT_EXECUTION_CAPABILITIES
        self._order_symbols: dict[str, str] = {}

    def place_order(self, request: OrderRequest) -> OrderAck:
        self.capabilities.require_order_type(request.instructions.order_type)
        if self.futures:
            venue_type = {
                OrderType.MARKET: "MARKET", OrderType.LIMIT: "LIMIT",
                OrderType.STOP: "STOP_MARKET", OrderType.STOP_LIMIT: "STOP",
            }[request.instructions.order_type]
        else:
            venue_type = {
                OrderType.MARKET: "MARKET", OrderType.LIMIT: "LIMIT",
                OrderType.STOP: "STOP_LOSS", OrderType.STOP_LIMIT: "STOP_LOSS_LIMIT",
            }[request.instructions.order_type]
            if request.instructions.post_only:
                venue_type = "LIMIT_MAKER"
        params = {
            "symbol": self._symbol(request.instrument_id),
            "side": request.side.value.upper(),
            "type": venue_type,
            "quantity": format(request.quantity, "f"),
            "newClientOrderId": request.client_order_id,
        }
        if request.instructions.limit_price is not None:
            params["price"] = format(request.instructions.limit_price, "f")
            if venue_type != "LIMIT_MAKER":
                params["timeInForce"] = "GTX" if self.futures and request.instructions.post_only else request.instructions.time_in_force.value.upper()
        if request.instructions.stop_price is not None:
            params["stopPrice"] = format(request.instructions.stop_price, "f")
        if self.futures:
            params["reduceOnly"] = str(request.instructions.reduce_only).lower()
            params["workingType"] = {
                "last": "CONTRACT_PRICE", "mark": "MARK_PRICE", "index": "MARK_PRICE",
            }[request.instructions.trigger_price_source.value]
        if request.instructions.iceberg_quantity is not None:
            params["icebergQty"] = format(request.instructions.iceberg_quantity, "f")
        if request.instructions.self_trade_prevention.value != "none":
            params["selfTradePreventionMode"] = request.instructions.self_trade_prevention.value.upper()
        signed, headers = self.signer.signed(params)
        path = self._path("/order")
        self.limiter.acquire()
        row = self.transport.request("POST", path, signed, headers)
        self._order_symbols[str(row["orderId"])] = params["symbol"]
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, str(row["orderId"]), datetime.now(timezone.utc),
        )

    def cancel_order(self, account, venue_order_id):
        symbol = self._order_symbols.get(venue_order_id)
        if symbol is None:
            raise LookupError(f"symbol unavailable for Binance order: {venue_order_id}")
        signed, headers = self.signer.signed({"orderId": venue_order_id, "symbol": symbol})
        self.limiter.acquire()
        self.transport.request("DELETE", self._path("/order"), signed, headers)

    def open_orders(self, account):
        signed, headers = self.signer.signed()
        self.limiter.acquire()
        rows = self.transport.request("GET", self._path("/openOrders"), signed, headers)
        return tuple(str(item["orderId"]) for item in rows)

    def set_leverage(self, symbol: str, leverage: int) -> None:
        if not self.futures or not 1 <= leverage <= 125:
            raise ValueError("futures leverage must be between 1 and 125")
        signed, headers = self.signer.signed({"symbol": symbol, "leverage": leverage})
        self.limiter.acquire()
        self.transport.request("POST", self._path("/leverage"), signed, headers)

    def set_margin_mode(self, symbol: str, isolated: bool) -> None:
        if not self.futures:
            raise ValueError("margin mode applies to futures only")
        signed, headers = self.signer.signed({"symbol": symbol, "marginType": "ISOLATED" if isolated else "CROSSED"})
        self.limiter.acquire()
        self.transport.request("POST", self._path("/marginType"), signed, headers)

    def set_position_mode(self, hedge_mode: bool) -> None:
        if not self.futures:
            raise ValueError("position mode applies to futures only")
        signed, headers = self.signer.signed({"dualSidePosition": str(hedge_mode).lower()})
        self.limiter.acquire()
        self.transport.request("POST", self._path("/positionSide/dual"), signed, headers)

    def _path(self, suffix: str) -> str:
        return ("/dapi/v1" if self.inverse else "/fapi/v1") + suffix if self.futures else "/api/v3" + suffix

    def _symbol(self, instrument_id: InstrumentId) -> str:
        try:
            return self.instrument_symbols[instrument_id]
        except KeyError as error:
            raise LookupError(f"Binance symbol mapping unavailable for {instrument_id}") from error


class BinanceOptionsExecutionAdapter:
    venue_id = VenueId("binance")
    capabilities = BINANCE_OPTIONS_EXECUTION_CAPABILITIES

    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, environment: Environment, limiter: RateLimiter | None = None, instrument_symbols: dict[InstrumentId, str] | None = None) -> None:
        if environment is not Environment.LIVE:
            raise ValueError("Binance options execution is live-only; no equivalent options testnet is available")
        self.transport, self.signer, self.environment = transport, signer, environment
        self.limiter = limiter or RateLimiter(1200, 60)
        self.instrument_symbols = dict(instrument_symbols or {})
        self._order_symbols: dict[str, str] = {}

    def place_order(self, request: OrderRequest) -> OrderAck:
        self.capabilities.require_order_type(request.instructions.order_type)
        if request.instructions.limit_price is None:
            raise ValueError("Binance options limit order requires a price")
        params = {
            "symbol": self._symbol(request.instrument_id),
            "side": request.side.value.upper(),
            "type": "LIMIT",
            "quantity": format(request.quantity, "f"),
            "price": format(request.instructions.limit_price, "f"),
            "timeInForce": request.instructions.time_in_force.value.upper(),
            "clientOrderId": request.client_order_id,
            "reduceOnly": str(request.instructions.reduce_only).lower(),
            "postOnly": str(request.instructions.post_only).lower(),
        }
        signed, headers = self.signer.signed(params)
        self.limiter.acquire()
        row = self.transport.request("POST", "/eapi/v1/order", signed, headers)
        venue_order_id = str(row["orderId"])
        self._order_symbols[venue_order_id] = params["symbol"]
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, venue_order_id, datetime.now(timezone.utc),
        )

    def cancel_order(self, account, venue_order_id):
        symbol = self._order_symbols.get(venue_order_id)
        if symbol is None:
            raise LookupError(f"symbol unavailable for Binance options order: {venue_order_id}")
        signed, headers = self.signer.signed({"symbol": symbol, "orderId": venue_order_id})
        self.limiter.acquire()
        self.transport.request("DELETE", "/eapi/v1/order", signed, headers)

    def open_orders(self, account):
        signed, headers = self.signer.signed()
        self.limiter.acquire()
        rows = self.transport.request("GET", "/eapi/v1/openOrders", signed, headers)
        return tuple(str(item["orderId"]) for item in rows)

    def _symbol(self, instrument_id: InstrumentId) -> str:
        try:
            return self.instrument_symbols[instrument_id]
        except KeyError as error:
            raise LookupError(f"Binance options symbol mapping unavailable for {instrument_id}") from error


class BinanceAccountAdapter:
    venue_id = VenueId("binance")

    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, environment: Environment, *, futures: bool = False, inverse: bool = False, limiter: RateLimiter | None = None, instrument_lookup: dict[str, InstrumentId] | None = None) -> None:
        self.transport, self.signer, self.environment, self.futures, self.inverse = transport, signer, environment, futures, inverse
        self.limiter = limiter or RateLimiter(1200, 60)
        self.instrument_lookup = instrument_lookup or {}

    def account_state(self, account) -> AccountState:
        signed, headers = self.signer.signed()
        path = "/dapi/v1/account" if self.inverse else "/fapi/v2/account" if self.futures else "/api/v3/account"
        self.limiter.acquire()
        row = self.transport.request("GET", path, signed, headers)
        if self.futures:
            balances = tuple(
                VenueBalance(
                    AssetId(item["asset"]), Decimal(item["walletBalance"]),
                    Decimal(item.get("availableBalance", item["walletBalance"])),
                    Decimal(item["walletBalance"]) - Decimal(item.get("availableBalance", item["walletBalance"])),
                    Decimal(item.get("borrowed", "0")), collateral=Decimal(item.get("crossWalletBalance", item["walletBalance"])),
                )
                for item in row.get("assets", [])
            )
            positions = tuple((self.instrument_lookup.get(item["symbol"], InstrumentId(f"crypto:binance:perpetual:{item['symbol']}")), Decimal(item["positionAmt"])) for item in row.get("positions", []) if Decimal(item["positionAmt"]) != 0)
        else:
            balances = tuple(
                VenueBalance(AssetId(item["asset"]), Decimal(item["free"]) + Decimal(item["locked"]), Decimal(item["free"]), Decimal(item["locked"]))
                for item in row.get("balances", []) if Decimal(item["free"]) + Decimal(item["locked"]) != 0
            )
            positions = ()
        signed_orders, order_headers = self.signer.signed()
        open_orders_path = "/dapi/v1/openOrders" if self.inverse else "/fapi/v1/openOrders" if self.futures else "/api/v3/openOrders"
        self.limiter.acquire()
        open_orders = self.transport.request("GET", open_orders_path, signed_orders, order_headers)
        return AccountState(account, balances, positions, tuple(str(item["orderId"]) for item in open_orders), datetime.now(timezone.utc))


class BinanceOptionsAccountAdapter:
    venue_id = VenueId("binance")

    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, environment: Environment, limiter: RateLimiter | None = None, instrument_lookup: dict[str, InstrumentId] | None = None) -> None:
        if environment is not Environment.LIVE:
            raise ValueError("Binance options account is live-only; no equivalent options testnet is available")
        self.transport, self.signer, self.environment = transport, signer, environment
        self.limiter = limiter or RateLimiter(1200, 60)
        self.instrument_lookup = instrument_lookup or {}

    def account_state(self, account) -> AccountState:
        signed, headers = self.signer.signed()
        self.limiter.acquire()
        account_row = self.transport.request("GET", "/eapi/v1/account", signed, headers)
        balances = tuple(
            VenueBalance(
                AssetId(item["asset"]),
                Decimal(item.get("marginBalance", item.get("equity", item.get("available", "0")))),
                Decimal(item.get("available", "0")),
                Decimal(item.get("locked", "0")),
            )
            for item in account_row.get("asset", account_row.get("assets", []))
        )
        signed_positions, position_headers = self.signer.signed()
        self.limiter.acquire()
        position_rows = self.transport.request("GET", "/eapi/v1/position", signed_positions, position_headers)
        positions = tuple(
            (self.instrument_lookup[item["symbol"]], Decimal(item.get("quantity", item.get("positionAmt", "0"))))
            for item in position_rows
            if item.get("symbol") in self.instrument_lookup and Decimal(item.get("quantity", item.get("positionAmt", "0"))) != 0
        )
        signed_orders, order_headers = self.signer.signed()
        self.limiter.acquire()
        open_orders = self.transport.request("GET", "/eapi/v1/openOrders", signed_orders, order_headers)
        return AccountState(
            account, balances, positions, tuple(str(item["orderId"]) for item in open_orders),
            datetime.now(timezone.utc),
        )


def websocket_url(environment: Environment, stream: str, *, futures: bool = False) -> str:
    if futures:
        host = "wss://stream.binancefuture.com/ws" if environment is Environment.TESTNET else "wss://fstream.binance.com/ws"
    else:
        host = "wss://testnet.binance.vision/ws" if environment is Environment.TESTNET else "wss://stream.binance.com:9443/ws"
    return f"{host}/{stream}"


def parse_market_stream_event(row: dict[str, Any], instrument_lookup: dict[str, InstrumentId]):
    payload = row.get("data", row)
    event_type = payload.get("e")
    symbol = payload.get("s") or payload.get("symbol")
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


def parse_user_stream_event(row: dict[str, Any], account, instrument_lookup: dict[str, InstrumentId]):
    event_type = row.get("e")
    if event_type == "executionReport" and row.get("x") == "TRADE":
        symbol = row["s"]
        return UserFillUpdate(
            str(row["t"]), str(row["i"]), account, instrument_lookup[symbol], row["S"].lower(),
            Decimal(row["l"]), Decimal(row["L"]), Decimal(row["n"]), AssetId(row["N"]),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    if event_type == "outboundAccountPosition":
        return BalanceUpdate(
            tuple((AssetId(item["a"]), Decimal(item["f"]), Decimal(item["l"])) for item in row["B"]),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    if event_type == "ORDER_TRADE_UPDATE" and row.get("o", {}).get("x") == "TRADE":
        order = row["o"]
        symbol = order["s"]
        return UserFillUpdate(
            str(order["t"]), str(order["i"]), account, instrument_lookup[symbol], order["S"].lower(),
            Decimal(order["l"]), Decimal(order["L"]), Decimal(order.get("n", "0")),
            AssetId(order.get("N") or order.get("ma") or "USDT"),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    if event_type == "ACCOUNT_UPDATE":
        balances = row.get("a", {}).get("B", [])
        return BalanceUpdate(
            tuple((AssetId(item["a"]), Decimal(item["wb"]), Decimal("0")) for item in balances),
            datetime.fromtimestamp(row["E"] / 1000, timezone.utc),
        )
    return None


class BinanceUserStreamProcessor:
    def __init__(self, account: AccountKey, instrument_lookup: dict[str, InstrumentId]) -> None:
        self.account, self.instrument_lookup = account, instrument_lookup
        self._execution_ids: set[str] = set()

    def process(self, row: dict[str, Any]):
        event = parse_user_stream_event(row, self.account, self.instrument_lookup)
        if isinstance(event, UserFillUpdate):
            if event.execution_id in self._execution_ids:
                return None
            self._execution_ids.add(event.execution_id)
        return event


def synchronize_clock(transport: BinanceTransport, signer: BinanceSigner, limiter: RateLimiter, *, futures: bool = False, inverse: bool = False, local_time_ms: int | None = None) -> int:
    path = "/dapi/v1/time" if inverse else "/fapi/v1/time" if futures else "/api/v3/time"
    limiter.acquire()
    row = transport.request("GET", path)
    return signer.synchronize(int(row["serverTime"]), local_time_ms)


def parse_option_market_snapshot(row: dict[str, Any], instrument_lookup: dict[str, InstrumentId]) -> OptionMarketSnapshot:
    symbol = row.get("symbol") or row.get("s")
    if symbol not in instrument_lookup:
        raise LookupError(f"unknown option symbol: {symbol}")
    timestamp_ms = row.get("eventTime") or row.get("E") or int(time() * 1000)
    return OptionMarketSnapshot(
        instrument_lookup[symbol], _decimal(row.get("bidPrice") or row.get("b")),
        _decimal(row.get("askPrice") or row.get("a")), _decimal(row.get("markPrice") or row.get("mp")),
        _decimal(row.get("indexPrice") or row.get("bo")), _decimal(row.get("volatility") or row.get("vo")),
        _decimal(row.get("delta") or row.get("d")), _decimal(row.get("gamma") or row.get("g")),
        _decimal(row.get("theta") or row.get("t")), _decimal(row.get("vega") or row.get("v")),
        datetime.fromtimestamp(int(timestamp_ms) / 1000, timezone.utc),
    )


class BinanceRecoveryService:
    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, execution: BinanceExecutionAdapter, account_adapter: BinanceAccountAdapter, processor: BinanceUserStreamProcessor, limiter: RateLimiter | None = None) -> None:
        self.transport, self.signer, self.execution, self.account_adapter, self.processor = transport, signer, execution, account_adapter, processor
        self.limiter = limiter or execution.limiter

    def recover(self, account: AccountKey, *, since_ms: int) -> RecoverySnapshot:
        state = self.account_adapter.account_state(account)
        open_order_ids = self.execution.open_orders(account)
        path = "/dapi/v1/userTrades" if self.execution.inverse else "/fapi/v1/userTrades" if self.execution.futures else "/api/v3/myTrades"
        signed, headers = self.signer.signed({"startTime": since_ms})
        self.limiter.acquire()
        rows = self.transport.request("GET", path, signed, headers)
        fills = []
        for row in rows:
            normalized = {
                "e": "executionReport", "x": "TRADE", "s": row["symbol"],
                "t": row.get("id") or row.get("tradeId"), "i": row.get("orderId"),
                "S": "BUY" if row.get("isBuyer", row.get("side") == "BUY") else "SELL",
                "l": row.get("qty") or row.get("executedQty"), "L": row.get("price"),
                "n": row.get("commission", "0"), "N": row.get("commissionAsset") or row.get("quoteAsset") or "USDT",
                "E": row.get("time") or row.get("updateTime"),
            }
            event = self.processor.process(normalized)
            if isinstance(event, UserFillUpdate):
                fills.append(event)
        return RecoverySnapshot(open_order_ids, tuple(fills), state)


class BinanceFundingAdapter:
    venue_id = VenueId("binance")

    def __init__(self, transport: BinanceTransport, signer: BinanceSigner, environment: Environment, *, inverse: bool = False, limiter: RateLimiter | None = None, instrument_lookup: dict[str, InstrumentId] | None = None) -> None:
        if environment not in {Environment.TESTNET, Environment.LIVE}:
            raise ValueError("Binance funding history requires testnet or live")
        self.transport, self.signer, self.environment, self.inverse = transport, signer, environment, inverse
        self.limiter = limiter or RateLimiter(1200, 60)
        self.instrument_lookup = instrument_lookup or {}

    def funding_history(self, account: AccountKey, start: datetime, end: datetime) -> tuple[FundingPayment, ...]:
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError("funding history requires an aware, increasing time range")
        signed, headers = self.signer.signed({
            "incomeType": "FUNDING_FEE",
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
        })
        self.limiter.acquire()
        path = "/dapi/v1/income" if self.inverse else "/fapi/v1/income"
        rows = self.transport.request("GET", path, signed, headers)
        payments = []
        for row in rows:
            symbol = row.get("symbol")
            instrument_id = self.instrument_lookup.get(symbol)
            if instrument_id is None:
                raise LookupError(f"unknown Binance funding instrument: {symbol}")
            external_id = row.get("tranId") or row.get("tradeId") or f"{symbol}:{row['time']}:{row['income']}"
            payments.append(FundingPayment(
                uuid5(NAMESPACE_URL, f"binance-funding:{external_id}"),
                datetime.fromtimestamp(int(row["time"]) / 1000, timezone.utc),
                account, instrument_id, AssetId(row["asset"]), Decimal(row["income"]),
                Decimal(row.get("fundingRate", "0")), Decimal(row.get("positionNotional", "0")),
            ))
        return tuple(sorted(payments, key=lambda item: (item.timestamp, str(item.payment_id))))



class BinanceStreamSession:
    """Injectable reconnecting stream loop; recovery callbacks perform REST backfill."""

    def __init__(self, connector: WebSocketConnector, url: str, *, maximum_reconnects: int = 5) -> None:
        self.connector, self.url, self.maximum_reconnects = connector, url, maximum_reconnects

    def consume(self, handler, *, message_limit: int, on_reconnect=None) -> int:
        if message_limit < 1:
            raise ValueError("message_limit must be positive")
        received = reconnects = 0
        connection = None
        try:
            while received < message_limit:
                if connection is None:
                    connection = self.connector.connect(self.url)
                try:
                    raw = connection.receive()
                    handler(json.loads(raw) if isinstance(raw, str) else raw)
                    received += 1
                except (ConnectionError, EOFError):
                    connection.close()
                    connection = None
                    reconnects += 1
                    if reconnects > self.maximum_reconnects:
                        raise ConnectionError("Binance stream reconnect limit exceeded")
                    if on_reconnect is not None:
                        on_reconnect(reconnects)
        finally:
            if connection is not None:
                connection.close()
        return received


def _spot_definition(row):
    filters = {item["filterType"]: item for item in row["filters"]}
    lot, price = filters["LOT_SIZE"], filters["PRICE_FILTER"]
    notional = filters.get("MIN_NOTIONAL", {}).get("minNotional")
    symbol = row["symbol"]
    return InstrumentDefinition(
        InstrumentId(f"crypto:binance:spot:{symbol}"), ProductType.CRYPTO_SPOT, symbol,
        AssetId(row["baseAsset"]), AssetId(row["quoteAsset"]),
        CryptoSpotSpec(AssetId(row["baseAsset"]), AssetId(row["quoteAsset"]), _decimal(notional)),
        (VenueListing(VenueId("binance"), symbol, symbol, Decimal(price["tickSize"]), Decimal(lot["stepSize"]), Decimal(lot["minQty"]), _decimal(notional)),),
        datetime.now(timezone.utc),
    )


def _perpetual_definition(row, inverse=False):
    filters = {item["filterType"]: item for item in row["filters"]}
    lot, price = filters["LOT_SIZE"], filters["PRICE_FILTER"]
    symbol = row["symbol"]
    settlement = row.get("marginAsset") or row.get("quoteAsset")
    return InstrumentDefinition(
        InstrumentId(f"crypto:binance:perpetual:{symbol}"), ProductType.PERPETUAL, symbol,
        AssetId(row["baseAsset"]), AssetId(row["quoteAsset"]),
        PerpetualSpec(AssetId(row["baseAsset"]), AssetId(settlement), row.get("pair", symbol), Decimal(row.get("contractSize", "1")), ContractType.INVERSE if inverse else ContractType.LINEAR, 28800),
        (VenueListing(VenueId("binance"), symbol, symbol, Decimal(price["tickSize"]), Decimal(lot["stepSize"]), Decimal(lot["minQty"])),),
        datetime.now(timezone.utc),
    )


def _future_definition(row, inverse=False):
    filters = {item["filterType"]: item for item in row["filters"]}
    lot, price = filters["LOT_SIZE"], filters["PRICE_FILTER"]
    symbol = row["symbol"]
    settlement = row.get("marginAsset") or row.get("quoteAsset")
    expiry_ms = row.get("deliveryDate") or row.get("deliveryTime")
    if expiry_ms is None:
        raise ValueError(f"delivery future is missing expiry: {symbol}")
    expiry = datetime.fromtimestamp(int(expiry_ms) / 1000, timezone.utc)
    return InstrumentDefinition(
        InstrumentId(f"crypto:binance:future:{symbol}"), ProductType.FUTURE, symbol,
        AssetId(row["baseAsset"]), AssetId(row["quoteAsset"]),
        FutureSpec(
            AssetId(row["baseAsset"]), AssetId(settlement), expiry,
            Decimal(row.get("contractSize", "1")), ContractType.INVERSE if inverse else ContractType.LINEAR,
            row.get("pair", symbol),
        ),
        (VenueListing(VenueId("binance"), symbol, symbol, Decimal(price["tickSize"]), Decimal(lot["stepSize"]), Decimal(lot["minQty"])),),
        datetime.now(timezone.utc),
    )


def _option_definition(row):
    symbol = row["symbol"]
    expiry = datetime.fromtimestamp(int(row["expiryDate"]) / 1000, timezone.utc)
    return InstrumentDefinition(
        InstrumentId(f"crypto:binance:option:{symbol}"), ProductType.CRYPTO_OPTION, symbol,
        AssetId(row["underlying"]), AssetId(row["quoteAsset"]),
        CryptoOptionSpec(AssetId(row["underlying"]), AssetId(row["quoteAsset"]), AssetId(row["settleAsset"]), AssetId(row["quoteAsset"]), expiry, Decimal(row["strikePrice"]), OptionRight.CALL if row["side"] == "CALL" else OptionRight.PUT, ExerciseStyle.EUROPEAN, Decimal(row.get("unit", "1")), row.get("underlying", "")),
        (VenueListing(VenueId("binance"), symbol, symbol, Decimal(row.get("priceScale", "0.01")), Decimal(row.get("quantityScale", "0.01")), Decimal(row.get("minQty", "0.01"))),),
        datetime.now(timezone.utc),
    )


def _decimal(value):
    return Decimal(str(value)) if value not in (None, "") else None
