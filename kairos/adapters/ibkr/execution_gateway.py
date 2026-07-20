from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairos.ports import (
    ComboOrderRequest,
    Environment,
    OrderAck,
    OrderRequest,
    RecoveredExecution,
    VenueOrderRecovery,
    VenueOrderStatus,
)
from kairos.domain.capability import ExecutionCapabilities, MarginMode, OrderType, PositionMode
from kairos.domain.execution import TradeExecution, TradeSide
from kairos.domain.identity import AssetId, InstitutionId, InstrumentId, VenueId
from kairos.domain.product import ProductType

from .session import IbkrSession


IBKR_EXECUTION_CAPABILITIES = ExecutionCapabilities(
    frozenset({OrderType.MARKET, OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT}),
    product_types=frozenset({ProductType.EQUITY, ProductType.ETF, ProductType.LISTED_OPTION}),
    supports_combo_orders=True,
    margin_modes=frozenset({MarginMode.NONE, MarginMode.SECURITIES}),
    position_modes=frozenset({PositionMode.ONE_WAY}),
)


class IbkrExecutionGateway:
    institution_id = InstitutionId("ibkr")
    venue_id = VenueId("ibkr")
    capabilities = IBKR_EXECUTION_CAPABILITIES

    def __init__(self, session: IbkrSession, environment: Environment) -> None:
        if environment not in {Environment.PAPER, Environment.LIVE}:
            raise ValueError("IBKR supports paper or live environment")
        self.session, self.environment = session, environment

    def place_order(self, request: OrderRequest) -> OrderAck:
        from ib_async import LimitOrder, MarketOrder, StopLimitOrder, StopOrder
        if self.environment is Environment.LIVE and self.session.readonly:
            raise PermissionError("readonly IBKR session cannot place live orders")
        self.capabilities.require_order_type(request.instructions.order_type)
        contract = self.session.contracts[request.instrument_id]
        action = request.side.value.upper()
        if request.instructions.order_type is OrderType.MARKET:
            order = MarketOrder(action, float(request.quantity), orderRef=request.client_order_id)
        elif request.instructions.order_type is OrderType.LIMIT:
            order = LimitOrder(action, float(request.quantity), float(request.instructions.limit_price), orderRef=request.client_order_id)
        elif request.instructions.order_type is OrderType.STOP:
            order = StopOrder(action, float(request.quantity), float(request.instructions.stop_price), orderRef=request.client_order_id)
        elif request.instructions.order_type is OrderType.STOP_LIMIT:
            order = StopLimitOrder(action, float(request.quantity), float(request.instructions.limit_price), float(request.instructions.stop_price), orderRef=request.client_order_id)
        else:
            raise ValueError(f"unsupported IBKR order type: {request.instructions.order_type}")
        trade = self.session.ib.placeOrder(contract, order)
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, str(trade.order.orderId), datetime.now(timezone.utc),
        )

    def cancel_order(self, account, venue_order_id):
        trade = next((item for item in self.session.ib.openTrades() if str(item.order.orderId) == venue_order_id), None)
        if trade is None:
            raise LookupError(f"open IBKR order not found: {venue_order_id}")
        self.session.ib.cancelOrder(trade.order)

    def open_orders(self, account):
        return tuple(str(item.order.orderId) for item in self.session.ib.openTrades())

    def recover_order(self, account, request, venue_order_id=None):
        self.session.connect()
        trades = list(self.session.ib.openTrades())
        all_trades = getattr(self.session.ib, "trades", None)
        if callable(all_trades):
            trades.extend(all_trades())
        trade = next((
            item for item in trades
            if (
                venue_order_id is not None and str(item.order.orderId) == venue_order_id
            ) or getattr(item.order, "orderRef", None) == request.client_order_id
        ), None)
        if trade is None:
            return VenueOrderRecovery(VenueOrderStatus.UNKNOWN, "IBKR order absent from synchronized trade set")
        order_id = str(trade.order.orderId)
        raw_status = str(getattr(getattr(trade, "orderStatus", None), "status", "Submitted"))
        status = _ibkr_order_status(raw_status)
        ack = OrderAck(
            request.internal_order_id,
            request.client_order_id,
            request.strategy_id,
            request.intent_id,
            request.correlation_id,
            order_id,
            _ibkr_trade_time(trade),
        )
        executions = ()
        if status in {VenueOrderStatus.PARTIALLY_FILLED, VenueOrderStatus.FILLED}:
            fills = list(getattr(trade, "fills", ()))
            all_fills = getattr(self.session.ib, "fills", None)
            if not fills and callable(all_fills):
                fills = [
                    fill for fill in all_fills()
                    if str(getattr(fill.execution, "orderId", "")) == order_id
                ]
            executions = _ibkr_recovered_executions(
                fills, account, request, status, contracts=self.session.contracts,
            )
        return VenueOrderRecovery(
            status,
            f"IBKR synchronized trade status={raw_status} orderId={order_id}",
            acknowledgement=ack,
            executions=executions,
        )

    def place_combo_order(self, request: ComboOrderRequest) -> OrderAck:
        from ib_async import ComboLeg, Contract, LimitOrder, MarketOrder
        if self.environment is Environment.LIVE and self.session.readonly:
            raise PermissionError("readonly IBKR session cannot place live orders")
        if len(request.legs) < 2:
            raise ValueError("combo order requires at least two legs")
        contracts = [self.session.contracts[leg.instrument_id] for leg in request.legs]
        combo = Contract(
            symbol=contracts[0].symbol, secType="BAG", currency=contracts[0].currency,
            exchange="SMART", comboLegs=[
                ComboLeg(contract.conId, leg.ratio, leg.side.value.upper(), contract.exchange or "SMART")
                for leg, contract in zip(request.legs, contracts)
            ],
        )
        if request.instructions.order_type is OrderType.MARKET:
            order = MarketOrder("BUY", float(request.quantity), orderRef=request.client_order_id)
        elif request.instructions.order_type is OrderType.LIMIT:
            order = LimitOrder("BUY", float(request.quantity), float(request.instructions.limit_price), orderRef=request.client_order_id)
        else:
            raise ValueError("IBKR combo supports market or limit orders")
        trade = self.session.ib.placeOrder(combo, order)
        return OrderAck(
            request.internal_order_id, request.client_order_id, request.strategy_id,
            request.intent_id, request.correlation_id, str(trade.order.orderId), datetime.now(timezone.utc),
        )


def _ibkr_order_status(value: str) -> VenueOrderStatus:
    normalized = value.replace(" ", "").lower()
    if normalized in {"pendingsubmit", "presubmitted", "submitted", "pendingcancel"}:
        return VenueOrderStatus.ACKNOWLEDGED
    if normalized in {"partiallyfilled", "partial"}:
        return VenueOrderStatus.PARTIALLY_FILLED
    if normalized == "filled":
        return VenueOrderStatus.FILLED
    if normalized in {"cancelled", "apicancelled"}:
        return VenueOrderStatus.CANCELLED
    if normalized in {"inactive", "rejected"}:
        return VenueOrderStatus.REJECTED
    return VenueOrderStatus.UNKNOWN


def _ibkr_trade_time(trade) -> datetime:
    log = getattr(trade, "log", ())
    value = getattr(log[0], "time", None) if log else None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _ibkr_recovered_executions(fills, account, request: OrderRequest | ComboOrderRequest, status, *, contracts):
    ordered = sorted(fills, key=lambda fill: (
        getattr(fill.execution, "time", datetime.min.replace(tzinfo=timezone.utc)),
        str(getattr(fill.execution, "execId", "")),
    ))
    recovered = []
    for index, fill in enumerate(ordered):
        execution_row = fill.execution
        exec_id = str(getattr(execution_row, "execId", ""))
        if not exec_id:
            raise ValueError("IBKR recovered fill is missing execId")
        timestamp = getattr(execution_row, "time", None)
        if not isinstance(timestamp, datetime):
            raise ValueError("IBKR recovered fill is missing execution time")
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        fallback_side = request.side.value if isinstance(request, OrderRequest) else ""
        side_value = str(getattr(execution_row, "side", None) or fallback_side).lower()
        if side_value not in {"buy", "bot", "sell", "sld"}:
            raise ValueError("IBKR recovered fill has an unsupported side")
        side = TradeSide.BUY if side_value in {"buy", "bot"} else TradeSide.SELL
        commission = getattr(fill, "commissionReport", None)
        if commission is None:
            raise ValueError("IBKR recovered fill is missing its commission report")
        fee = Decimal(str(getattr(commission, "commission", 0) or 0))
        fee_asset = AssetId(str(getattr(commission, "currency", "USD") or "USD"))
        if isinstance(request, ComboOrderRequest):
            fill_contract = getattr(fill, "contract", None)
            contract_id = getattr(fill_contract, "conId", None)
            instrument_id = next((
                instrument for instrument, contract in contracts.items()
                if getattr(contract, "conId", None) == contract_id
            ), None)
            if instrument_id is None or instrument_id not in {leg.instrument_id for leg in request.legs}:
                raise ValueError("IBKR combo fill contract cannot be mapped to a requested leg")
        else:
            instrument_id = request.instrument_id
        execution = TradeExecution(
            uuid5(NAMESPACE_URL, f"ibkr:execution:{exec_id}"),
            timestamp,
            account,
            instrument_id,
            side,
            Decimal(str(getattr(execution_row, "shares"))),
            Decimal(str(getattr(execution_row, "price"))),
            fee_asset,
            fee,
            request.client_order_id,
        )
        recovered.append(RecoveredExecution(
            f"ibkr:execution:{exec_id}",
            execution,
            status is VenueOrderStatus.FILLED and index == len(ordered) - 1,
            f"ibkr:fills:{account.value}",
            f"{timestamp.isoformat()}:{exec_id}",
        ))
    return tuple(recovered)


def normalize_ibkr_execution(*, execution_id: str, timestamp: datetime, account, instrument_id: InstrumentId, side: str, quantity, price, commission, commission_currency: str, order_id: str) -> TradeExecution:
    """Normalize IBKR execution/commission callbacks without exposing SDK objects."""
    return TradeExecution(
        uuid5(NAMESPACE_URL, f"ibkr-execution:{execution_id}"), timestamp, account, instrument_id,
        TradeSide.BUY if side.upper() in {"BOT", "BUY"} else TradeSide.SELL,
        Decimal(str(quantity)), Decimal(str(price)), AssetId(commission_currency),
        abs(Decimal(str(commission))), order_id,
    )


IbkrExecutionAdapter = IbkrExecutionGateway
