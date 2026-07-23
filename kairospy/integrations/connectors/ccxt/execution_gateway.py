from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from kairospy.environment import Environment
from kairospy.execution.events import TradeExecution, TradeSide
from kairospy.execution.orders import ExecutionCapabilities, MarginMode, OrderType, PositionMode
from kairospy.execution.ports import (
    OrderAck,
    OrderRequest,
    RecoveredExecution,
    VenueOrderRecovery,
    VenueOrderStatus,
)
from kairospy.identity import AccountRef, AssetId, InstitutionId, VenueId
from kairospy.reference.contracts import ProductType

from .errors import CcxtConnectorError
from .symbol_mapper import CcxtSymbolMapper


CCXT_EXECUTION_CAPABILITIES = ExecutionCapabilities(
    frozenset({OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT}),
    product_types=frozenset({ProductType.CRYPTO_SPOT, ProductType.PERPETUAL, ProductType.FUTURE}),
    supports_reduce_only=True,
    supports_post_only=True,
    margin_modes=frozenset({MarginMode.NONE, MarginMode.CROSS, MarginMode.ISOLATED}),
    position_modes=frozenset({PositionMode.ONE_WAY}),
)


class CcxtExecutionGateway:
    service_id = "execution"
    service_kind = "execution"

    def __init__(
        self,
        exchange: Any,
        *,
        provider: str,
        environment: Environment,
        symbol_mapper: CcxtSymbolMapper,
        capabilities: ExecutionCapabilities = CCXT_EXECUTION_CAPABILITIES,
    ) -> None:
        self.exchange = exchange
        self.institution_id = InstitutionId(provider)
        self.venue_id = VenueId(provider)
        self.environment = environment
        self.symbol_mapper = symbol_mapper
        self.capabilities = capabilities
        self._order_symbols: dict[str, str] = {}

    def place_order(self, request: OrderRequest) -> OrderAck:
        self.capabilities.require_order_type(request.instructions.order_type)
        symbol = self.symbol_mapper.symbol_for(request.instrument_id)
        params = _order_params(request)
        row = self.exchange.create_order(
            symbol,
            _order_type(request.instructions.order_type),
            request.side.value,
            float(request.quantity),
            float(request.instructions.limit_price) if request.instructions.limit_price is not None else None,
            params,
        )
        venue_order_id = _required_id(row)
        self._order_symbols[venue_order_id] = symbol
        return OrderAck(
            request.internal_order_id,
            request.client_order_id,
            request.strategy_id,
            request.intent_id,
            request.correlation_id,
            venue_order_id,
            _timestamp(row.get("timestamp"), datetime.now(timezone.utc)),
        )

    def cancel_order(self, account: AccountRef, venue_order_id: str) -> None:
        symbol = self._order_symbols.get(venue_order_id)
        if symbol is None:
            raise LookupError(f"symbol unavailable for CCXT order: {venue_order_id}")
        self.exchange.cancel_order(venue_order_id, symbol)

    def open_orders(self, account: AccountRef) -> tuple[str, ...]:
        rows = self.exchange.fetch_open_orders()
        return tuple(str(item["id"]) for item in rows if item.get("id") is not None)

    def recover_order(
        self,
        account: AccountRef,
        request: OrderRequest,
        venue_order_id: str | None = None,
    ) -> VenueOrderRecovery:
        symbol = self.symbol_mapper.symbol_for(request.instrument_id)
        order_id = venue_order_id or self._find_order_id(request.client_order_id, symbol)
        row = self.exchange.fetch_order(order_id, symbol)
        status = _order_status(str(row.get("status", "unknown")))
        ack = OrderAck(
            request.internal_order_id,
            request.client_order_id,
            request.strategy_id,
            request.intent_id,
            request.correlation_id,
            str(row.get("id") or order_id),
            _timestamp(row.get("timestamp"), datetime.now(timezone.utc)),
        )
        executions = ()
        if status in {VenueOrderStatus.PARTIALLY_FILLED, VenueOrderStatus.FILLED}:
            executions = _recovered_executions(
                self.exchange.fetch_my_trades(symbol=symbol, params={"order": str(row.get("id") or order_id)}),
                account,
                request,
                status,
            )
        self._order_symbols[str(row.get("id") or order_id)] = symbol
        return VenueOrderRecovery(
            status,
            f"CCXT order query status={row.get('status')} id={row.get('id') or order_id}",
            acknowledgement=ack,
            executions=executions,
        )

    def _find_order_id(self, client_order_id: str, symbol: str) -> str:
        rows = self.exchange.fetch_open_orders(symbol)
        for row in rows:
            if row.get("clientOrderId") == client_order_id:
                return str(row["id"])
        raise LookupError(f"CCXT order id unavailable for client order: {client_order_id}")


def _order_type(order_type: OrderType) -> str:
    return {
        OrderType.MARKET: "market",
        OrderType.LIMIT: "limit",
        OrderType.STOP: "stop",
        OrderType.STOP_LIMIT: "stop_limit",
    }[order_type]


def _order_params(request: OrderRequest) -> dict[str, object]:
    params: dict[str, object] = {"clientOrderId": request.client_order_id}
    instructions = request.instructions
    if instructions.time_in_force is not None:
        params["timeInForce"] = instructions.time_in_force.value.upper()
    if instructions.stop_price is not None:
        params["stopPrice"] = float(instructions.stop_price)
    if instructions.post_only:
        params["postOnly"] = True
    if instructions.reduce_only:
        params["reduceOnly"] = True
    if instructions.margin_mode is not MarginMode.NONE:
        params["marginMode"] = instructions.margin_mode.value
    if instructions.leverage is not None:
        params["leverage"] = float(instructions.leverage)
    return params


def _required_id(row: dict[str, Any]) -> str:
    value = row.get("id")
    if value in (None, ""):
        raise CcxtConnectorError("CCXT create_order response is missing id")
    return str(value)


def _order_status(value: str) -> VenueOrderStatus:
    normalized = value.lower().replace(" ", "_")
    if normalized in {"open", "new", "accepted"}:
        return VenueOrderStatus.ACKNOWLEDGED
    if normalized in {"partially_filled", "partial"}:
        return VenueOrderStatus.PARTIALLY_FILLED
    if normalized in {"closed", "filled"}:
        return VenueOrderStatus.FILLED
    if normalized == "canceled" or normalized == "cancelled":
        return VenueOrderStatus.CANCELLED
    if normalized in {"expired"}:
        return VenueOrderStatus.EXPIRED
    if normalized in {"rejected"}:
        return VenueOrderStatus.REJECTED
    return VenueOrderStatus.UNKNOWN


def _timestamp(value: Any, fallback: datetime) -> datetime:
    if value in (None, ""):
        return fallback
    return datetime.fromtimestamp(float(Decimal(str(value)) / Decimal("1000")), timezone.utc)


def _recovered_executions(
    trades: list[dict[str, Any]],
    account: AccountRef,
    request: OrderRequest,
    status: VenueOrderStatus,
) -> tuple[RecoveredExecution, ...]:
    recovered = []
    ordered = sorted(trades, key=lambda item: (item.get("timestamp") or 0, str(item.get("id") or "")))
    for index, row in enumerate(ordered):
        trade_id = str(row.get("id") or row.get("tradeId") or "")
        if not trade_id:
            raise CcxtConnectorError("CCXT trade response is missing id")
        fee = row.get("fee") or {}
        timestamp = _timestamp(row.get("timestamp"), datetime.now(timezone.utc))
        side = TradeSide(str(row.get("side") or request.side.value).lower())
        execution = TradeExecution(
            uuid5(NAMESPACE_URL, f"ccxt:execution:{request.account.value}:{trade_id}"),
            timestamp,
            account,
            request.instrument_id,
            side,
            Decimal(str(row["amount"])),
            Decimal(str(row["price"])),
            AssetId(str(fee.get("currency") or "USD")),
            abs(Decimal(str(fee.get("cost") or 0))),
            request.client_order_id,
        )
        recovered.append(RecoveredExecution(
            f"ccxt:execution:{trade_id}",
            execution,
            status is VenueOrderStatus.FILLED and index == len(ordered) - 1,
            f"ccxt:trades:{account.value}",
            f"{timestamp.isoformat()}:{trade_id}",
        ))
    return tuple(recovered)
