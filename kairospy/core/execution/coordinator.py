from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Mapping, Protocol
from uuid import uuid4

from kairospy.core.account import (
    AccountEvent,
    AccountEventKind,
    AccountContext,
    AccountLedger,
    AccountSnapshot,
    AccountProjection,
    Reservation,
    ReservationBook,
    project_account,
    reserve_cash_order,
    reserve_margin_order,
)
from kairospy.core.order import OrderEvent, OrderEventKind, OrderJournal, OrderRequest, OrderSide, OrderState, OrderStatus, OrderType

from .updates import ExecutionUpdate


class BrokerGateway(Protocol):
    def create_order(
        self,
        symbol: str,
        *,
        side: str,
        type: str,
        amount: object,
        price: object | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        ...

    def cancel_order(
        self,
        id: str,
        *,
        symbol: str | None = None,
        params: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        ...


@dataclass(frozen=True, slots=True)
class FillReport:
    client_order_id: str
    occurred_at: datetime
    fill_quantity: Decimal
    fill_price: Decimal
    settlement_currency: str
    cash_delta: Decimal
    fee_currency: str | None = None
    fee_amount: Decimal = Decimal("0")
    cumulative_filled_quantity: Decimal | None = None

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("fill timestamp must be timezone-aware")
        if self.fill_quantity <= 0 or self.fill_price <= 0:
            raise ValueError("fill quantity and price must be positive")
        if not self.settlement_currency.strip():
            raise ValueError("settlement currency cannot be empty")
        if self.fee_amount < 0:
            raise ValueError("fee amount cannot be negative")
        if self.fee_amount and not self.fee_currency:
            raise ValueError("fee currency is required when fee amount is positive")


class ExecutionCoordinator:
    def __init__(
        self,
        *,
        orders: OrderJournal | None = None,
        ledger: AccountLedger | None = None,
        reservations: ReservationBook | None = None,
        broker: BrokerGateway | None = None,
        broker_symbol_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self.orders = orders or OrderJournal()
        self.ledger = ledger or AccountLedger()
        self.reservations = reservations or ReservationBook()
        self.broker = broker
        self.broker_symbol_resolver = broker_symbol_resolver or (lambda instrument_id: instrument_id)

    def plan_order(
        self,
        request: OrderRequest,
        *,
        reserve_currency: str | None = None,
        reserve_amount: Decimal | None = None,
        margin_notional: Decimal | None = None,
        margin_leverage: Decimal = Decimal("1"),
        margin_instrument_id: str | None = None,
        venue_snapshot: AccountSnapshot | None = None,
        at: datetime,
    ) -> OrderState:
        if at.tzinfo is None:
            raise ValueError("plan timestamp must be timezone-aware")
        state = self.orders.plan(request)
        if reserve_currency is None and reserve_amount is None and margin_notional is None:
            return state
        if margin_notional is not None:
            if not reserve_currency:
                raise ValueError("reserve_currency is required for margin checks")
            if margin_notional <= 0:
                raise ValueError("margin_notional must be positive")
            if reserve_amount is not None:
                raise ValueError("reserve_amount and margin_notional cannot be supplied together")
        elif not reserve_currency or reserve_amount is None:
            raise ValueError("reserve_currency and reserve_amount must be supplied together")
        projection = project_account(
            request.context,
            ledger=self.ledger,
            venue=venue_snapshot,
            reservations=self.reservations,
        )
        amount = reserve_amount
        if margin_notional is not None:
            amount = margin_notional / margin_leverage
        reservation = Reservation(
            request.reservation_id or request.client_order_id,
            request.context.account,
            reserve_currency,
            amount,
            "order margin pre-submit hold" if margin_notional is not None else "order pre-submit hold",
            at,
            order_id=request.client_order_id,
        )
        if margin_notional is None:
            check = reserve_cash_order(self.reservations, reservation, projection)
        else:
            check = reserve_margin_order(
                self.reservations,
                reservation,
                projection,
                instrument_id=margin_instrument_id or request.instrument_id,
                notional=margin_notional,
                leverage=margin_leverage,
            )
        if not check.accepted:
            self.orders.record(OrderEvent(request.client_order_id, OrderEventKind.REJECTED, at, reason=check.reason))
            return self.orders.get(request.client_order_id)
        return self.orders.record(OrderEvent(request.client_order_id, OrderEventKind.RESERVED, at))

    def submit_order(
        self,
        client_order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        if at.tzinfo is None:
            raise ValueError("submit timestamp must be timezone-aware")
        state = self.orders.record(OrderEvent(client_order_id, OrderEventKind.SUBMITTED, at))
        if self.broker is None:
            return state
        try:
            response = self.broker.create_order(
                self.broker_symbol(state.request.market_id or state.request.instrument_id),
                side=state.request.side.value,
                type=state.request.order_type.value,
                amount=state.request.quantity,
                price=state.request.limit_price,
                params=params,
            )
        except Exception as error:
            return self.orders.record(OrderEvent(client_order_id, OrderEventKind.UNKNOWN, at, reason=str(error)))
        venue_order_id = str(response.get("id") or response.get("orderId") or "")
        if not venue_order_id:
            return self.orders.record(OrderEvent(client_order_id, OrderEventKind.UNKNOWN, at, reason="missing venue order id"))
        return self.orders.record(OrderEvent(client_order_id, OrderEventKind.ACKNOWLEDGED, at, venue_order_id=venue_order_id))

    def mark_reservation_reflected(self, client_order_id: str) -> None:
        state = self.orders.get(client_order_id)
        reservation_id = state.request.reservation_id or client_order_id
        self.reservations.reflect(reservation_id)

    def account_projection(
        self,
        context: AccountContext,
        *,
        venue_snapshot: AccountSnapshot | None = None,
    ) -> AccountProjection:
        return project_account(
            context,
            ledger=self.ledger,
            venue=venue_snapshot,
            reservations=self.reservations,
            local_orders=self.orders.active_for_context(context),
        )

    def request_cancel(self, client_order_id: str, *, at: datetime) -> OrderState:
        return self.orders.record(OrderEvent(client_order_id, OrderEventKind.CANCEL_REQUESTED, at))

    def cancel_order(
        self,
        client_order_id: str,
        *,
        at: datetime,
        params: Mapping[str, object] | None = None,
    ) -> OrderState:
        state = self.request_cancel(client_order_id, at=at)
        if self.broker is None:
            return state
        if not state.venue_order_id:
            return self.orders.record(
                OrderEvent(client_order_id, OrderEventKind.UNKNOWN, at, reason="missing venue order id for cancel")
            )
        try:
            response = self.broker.cancel_order(
                state.venue_order_id,
                symbol=self.broker_symbol(state.request.market_id or state.request.instrument_id),
                params=params,
            )
        except Exception as error:
            return self.orders.record(OrderEvent(client_order_id, OrderEventKind.UNKNOWN, at, reason=str(error)))
        status = str(response.get("status") or "").strip().lower()
        if status in {"canceled", "cancelled"}:
            return self.cancel_confirmed(client_order_id, at=at)
        return state

    def cancel_confirmed(self, client_order_id: str, *, at: datetime) -> OrderState:
        state = self.orders.record(OrderEvent(client_order_id, OrderEventKind.CANCELED, at))
        self._release_reservation(state)
        return state

    def ingest_fill(self, report: FillReport) -> OrderState:
        state = self.orders.get(report.client_order_id)
        cumulative = report.cumulative_filled_quantity or state.filled_quantity + report.fill_quantity
        kind = OrderEventKind.FILLED if cumulative >= state.request.quantity else OrderEventKind.PARTIALLY_FILLED
        updated = self.orders.record(
            OrderEvent(report.client_order_id, kind, report.occurred_at, filled_quantity=cumulative)
        )
        self.ledger.record(
            AccountEvent(
                uuid4(),
                state.request.context.account,
                AccountEventKind.FILL,
                report.occurred_at,
                report.settlement_currency,
                cash_delta=report.cash_delta,
                instrument_id=state.request.instrument_id,
                position_delta=report.fill_quantity * Decimal(state.request.side.position_sign),
                reference_id=report.client_order_id,
            )
        )
        if report.fee_amount:
            self.ledger.record(
                AccountEvent(
                    uuid4(),
                    state.request.context.account,
                    AccountEventKind.FEE,
                    report.occurred_at,
                    report.fee_currency or report.settlement_currency,
                    cash_delta=-report.fee_amount,
                    reference_id=report.client_order_id,
                )
            )
        if updated.status.terminal:
            self._consume_reservation(updated)
        return updated

    def apply_execution_update(self, update: ExecutionUpdate) -> OrderState:
        state = _known_order(self.orders, update)
        if state is None:
            return self._import_execution_update(update)
        if update.fill_quantity is not None and update.fill_price is not None:
            return self.ingest_fill(
                FillReport(
                    state.request.client_order_id,
                    update.observed_at,
                    update.fill_quantity,
                    update.fill_price,
                    update.settlement_currency or _settlement_currency(state.request.instrument_id),
                    cash_delta=update.cash_delta
                    if update.cash_delta is not None
                    else _cash_delta(state.request.side, update.fill_quantity, update.fill_price),
                    fee_currency=update.fee_currency,
                    fee_amount=update.fee_amount,
                    cumulative_filled_quantity=update.filled_quantity,
                )
            )
        if update.kind is OrderEventKind.ACKNOWLEDGED and state.status is OrderStatus.ACKNOWLEDGED:
            return state
        return self.orders.record(
            OrderEvent(
                state.request.client_order_id,
                update.kind,
                update.observed_at,
                venue_order_id=update.venue_order_id or None,
                filled_quantity=update.filled_quantity
                if update.kind in {OrderEventKind.PARTIALLY_FILLED, OrderEventKind.FILLED}
                else None,
                reason=update.reason,
            )
        )

    def _release_reservation(self, state: OrderState) -> None:
        reservation_id = state.request.reservation_id or state.request.client_order_id
        try:
            self.reservations.release(reservation_id)
        except KeyError:
            return

    def broker_symbol(self, instrument_id: str) -> str:
        symbol = str(self.broker_symbol_resolver(instrument_id)).strip()
        if not symbol:
            raise ValueError(f"empty broker symbol for instrument: {instrument_id}")
        return symbol

    def _consume_reservation(self, state: OrderState) -> None:
        reservation_id = state.request.reservation_id or state.request.client_order_id
        try:
            self.reservations.consume(reservation_id)
        except KeyError:
            return

    def _import_execution_update(self, update: ExecutionUpdate) -> OrderState:
        if update.kind not in {OrderEventKind.ACKNOWLEDGED, OrderEventKind.PARTIALLY_FILLED}:
            raise LookupError(f"terminal execution update has no known local order: {update.venue_order_id}")
        if update.context is None:
            raise LookupError(f"execution update has no account context for unknown order: {update.venue_order_id}")
        if update.instrument_id is None or update.side is None or update.quantity is None or update.order_type is None:
            raise ValueError("execution update cannot import an unknown order without order identity fields")
        return self.orders.import_venue_open_order(
            context=update.context,
            venue_order_id=update.venue_order_id,
            instrument_id=update.instrument_id,
            market_id=update.market_id,
            side=update.side,
            quantity=update.quantity,
            order_type=update.order_type,
            limit_price=update.limit_price if update.order_type is OrderType.LIMIT else None,
            status=OrderStatus.PARTIALLY_FILLED if update.kind is OrderEventKind.PARTIALLY_FILLED else OrderStatus.ACKNOWLEDGED,
            filled_quantity=update.filled_quantity or Decimal("0"),
            observed_at=update.observed_at,
        )


def cash_order_request(
    *,
    client_order_id: str,
    context: AccountContext,
    instrument_id: str,
    side: OrderSide,
    quantity: Decimal,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Decimal | None = None,
) -> OrderRequest:
    return OrderRequest(client_order_id, context, instrument_id, side, quantity, order_type, limit_price)


def _known_order(orders: OrderJournal, update: ExecutionUpdate) -> OrderState | None:
    if update.client_order_id:
        try:
            return orders.get(update.client_order_id)
        except LookupError:
            pass
    if update.venue_order_id:
        try:
            return orders.get_by_venue_order_id(update.venue_order_id)
        except LookupError:
            pass
    return None


def _settlement_currency(symbol: str) -> str:
    if "/" not in symbol:
        parts = symbol.split(":")
        if len(parts) >= 4 and parts[0] == "instrument":
            return parts[-1].upper() or "USD"
        return "USD"
    return symbol.split("/", 1)[1].split(":", 1)[0] or "USD"


def _cash_delta(side: OrderSide, quantity: Decimal, price: Decimal) -> Decimal:
    cost = quantity * price
    return cost if side is OrderSide.SELL else -cost


__all__ = ["BrokerGateway", "ExecutionCoordinator", "FillReport", "cash_order_request"]
