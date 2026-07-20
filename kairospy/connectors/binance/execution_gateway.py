from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from kairospy.ports import (
    Environment,
    OrderAck,
    OrderRequest,
    RecoveredExecution,
    VenueOrderRecovery,
    VenueOrderStatus,
)
from kairospy.domain.capability import (
    ExecutionCapabilities,
    MarginMode,
    OrderType,
    PositionMode,
)
from kairospy.domain.execution import TradeExecution, TradeSide
from kairospy.domain.identity import AccountKey, AssetId, InstitutionId, InstrumentId, VenueId
from kairospy.domain.product import ProductType

from .request_signing import BinanceSigner
from .rest_transport import BinanceTransport, RateLimiter


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


class BinanceExecutionGateway:
    institution_id = InstitutionId("binance")
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

    def recover_order(self, account, request, venue_order_id=None):
        if not isinstance(request, OrderRequest):
            raise ValueError("Binance does not support native combo order recovery")
        symbol = self._symbol(request.instrument_id)
        params = {"symbol": symbol}
        if venue_order_id is not None:
            params["orderId"] = venue_order_id
        else:
            params["origClientOrderId"] = request.client_order_id
        signed, headers = self.signer.signed(params)
        self.limiter.acquire()
        row = self.transport.request("GET", self._path("/order"), signed, headers)
        status = _binance_order_status(str(row.get("status", "UNKNOWN")))
        ack = _binance_recovery_ack(request, row)
        executions = ()
        if status in {VenueOrderStatus.PARTIALLY_FILLED, VenueOrderStatus.FILLED}:
            trade_params = {"symbol": symbol, "orderId": str(row["orderId"])}
            signed_trades, trade_headers = self.signer.signed(trade_params)
            self.limiter.acquire()
            trades = self.transport.request("GET", self._path("/userTrades") if self.futures else "/api/v3/myTrades", signed_trades, trade_headers)
            executions = _binance_recovered_executions(
                trades, account, request, status, "futures" if self.futures else "spot",
            )
        self._order_symbols[str(row["orderId"])] = symbol
        return VenueOrderRecovery(
            status,
            f"Binance REST order query status={row.get('status')} orderId={row.get('orderId')}",
            acknowledgement=ack,
            executions=executions,
        )

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


class BinanceOptionsExecutionGateway:
    institution_id = InstitutionId("binance")
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

    def recover_order(self, account, request, venue_order_id=None):
        if not isinstance(request, OrderRequest):
            raise ValueError("Binance options do not support native combo order recovery")
        symbol = self._symbol(request.instrument_id)
        params = {"symbol": symbol}
        if venue_order_id is not None:
            params["orderId"] = venue_order_id
        else:
            params["clientOrderId"] = request.client_order_id
        signed, headers = self.signer.signed(params)
        self.limiter.acquire()
        row = self.transport.request("GET", "/eapi/v1/order", signed, headers)
        status = _binance_order_status(str(row.get("status", "UNKNOWN")))
        ack = _binance_recovery_ack(request, row)
        executions = ()
        if status in {VenueOrderStatus.PARTIALLY_FILLED, VenueOrderStatus.FILLED}:
            trade_params = {"symbol": symbol, "orderId": str(row["orderId"])}
            signed_trades, trade_headers = self.signer.signed(trade_params)
            self.limiter.acquire()
            trades = self.transport.request("GET", "/eapi/v1/userTrades", signed_trades, trade_headers)
            executions = _binance_recovered_executions(trades, account, request, status, "options")
        self._order_symbols[str(row["orderId"])] = symbol
        return VenueOrderRecovery(
            status,
            f"Binance Options REST order query status={row.get('status')} orderId={row.get('orderId')}",
            acknowledgement=ack,
            executions=executions,
        )

    def _symbol(self, instrument_id: InstrumentId) -> str:
        try:
            return self.instrument_symbols[instrument_id]
        except KeyError as error:
            raise LookupError(f"Binance options symbol mapping unavailable for {instrument_id}") from error


def _binance_order_status(value: str) -> VenueOrderStatus:
    normalized = value.upper()
    if normalized in {"NEW", "PENDING_NEW", "ACCEPTED"}:
        return VenueOrderStatus.ACKNOWLEDGED
    if normalized == "PARTIALLY_FILLED":
        return VenueOrderStatus.PARTIALLY_FILLED
    if normalized == "FILLED":
        return VenueOrderStatus.FILLED
    if normalized in {"CANCELED", "CANCELLED", "PENDING_CANCEL"}:
        return VenueOrderStatus.CANCELLED
    if normalized in {"EXPIRED", "EXPIRED_IN_MATCH"}:
        return VenueOrderStatus.EXPIRED
    if normalized in {"REJECTED", "FAILED"}:
        return VenueOrderStatus.REJECTED
    return VenueOrderStatus.UNKNOWN


def _binance_recovery_ack(request: OrderRequest, row: dict[str, Any]) -> OrderAck:
    timestamp_ms = row.get("transactTime", row.get("time", row.get("updateTime")))
    accepted_at = (
        datetime.fromtimestamp(int(timestamp_ms) / 1000, timezone.utc)
        if timestamp_ms is not None
        else datetime.now(timezone.utc)
    )
    return OrderAck(
        request.internal_order_id,
        request.client_order_id,
        request.strategy_id,
        request.intent_id,
        request.correlation_id,
        str(row["orderId"]),
        accepted_at,
    )


def _binance_recovered_executions(
    rows: list[dict[str, Any]],
    account: AccountKey,
    request: OrderRequest,
    status: VenueOrderStatus,
    product: str,
) -> tuple[RecoveredExecution, ...]:
    ordered = sorted(rows, key=lambda row: (int(row.get("time", 0)), str(row.get("id", row.get("tradeId", "")))))
    recovered = []
    for index, row in enumerate(ordered):
        trade_id = str(row.get("id", row.get("tradeId")))
        if not trade_id or trade_id == "None":
            raise ValueError("Binance recovered trade is missing a stable trade id")
        quantity = Decimal(str(row.get("qty", row.get("quantity", row.get("executedQty", "0")))))
        price = Decimal(str(row.get("price", "0")))
        if quantity <= 0 or price <= 0:
            raise ValueError("Binance recovered trade requires positive quantity and price")
        side_value = row.get("side")
        side = (
            TradeSide(str(side_value).lower())
            if side_value is not None
            else TradeSide.BUY if bool(row.get("isBuyer")) else TradeSide.SELL
        )
        event_ms = int(row.get("time", row.get("updateTime", 0)))
        if event_ms <= 0:
            raise ValueError("Binance recovered trade requires an event time")
        fee_asset = AssetId(str(row.get("commissionAsset", row.get("feeAsset", "UNKNOWN"))))
        fee = Decimal(str(row.get("commission", row.get("fee", "0"))))
        execution = TradeExecution(
            uuid5(NAMESPACE_URL, f"binance:{product}:trade:{trade_id}"),
            datetime.fromtimestamp(event_ms / 1000, timezone.utc),
            account,
            request.instrument_id,
            side,
            quantity,
            price,
            fee_asset,
            fee,
            request.client_order_id,
        )
        recovered.append(RecoveredExecution(
            f"binance:{product}:trade:{trade_id}",
            execution,
            status is VenueOrderStatus.FILLED and index == len(ordered) - 1,
            f"binance:{product}:fills:{account.value}",
            f"{event_ms}:{trade_id}",
        ))
    return tuple(recovered)
