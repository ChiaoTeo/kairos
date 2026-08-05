from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from kairospy.domain.account import (
    AccountEvent,
    AccountEventKind,
    AccountSegment,
    AccountRuntimeContext,
    AccountLedger,
    AccountSnapshot,
    AccountState,
    derive_account_state,
)
from kairospy.domain.order import (
    OrderEvent,
    OrderEventKind,
    OrderJournal,
    OrderRequest,
    OrderSide,
    OrderState,
    OrderStatus,
    OrderType,
)
from kairospy.domain.reference import InstrumentId

from kairospy.domain.execution import ExecutionUpdate, Reservation, ReservationBook, reserve_asset_order, reserve_margin_order
from kairospy.application.usecases.risk.application.budget import (
    RiskApplication,
    RiskAssessmentRequest,
    RiskReservationRequest,
)
from kairospy.application.usecases.risk.domain import BudgetRef, RiskDecision, RiskMetric, RiskUsage


@dataclass(frozen=True, slots=True)
class FillReport:
    order_id: str
    occurred_at: datetime
    fill_quantity: Decimal
    fill_price: Decimal
    settlement_currency: str
    balance_delta: Decimal
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
        risk: RiskApplication | None = None,
    ) -> None:
        self.orders = orders or OrderJournal()
        self.ledger = ledger or AccountLedger()
        self.reservations = reservations or ReservationBook()
        self.risk = risk

    def plan_order(
        self,
        request: OrderRequest,
        *,
        reserve_currency: str | None = None,
        reserve_amount: Decimal | None = None,
        margin_notional: Decimal | None = None,
        margin_leverage: Decimal = Decimal("1"),
        margin_instrument_id: InstrumentId | str | None = None,
        venue_snapshot: AccountSnapshot | None = None,
        risk_amount: Decimal | None = None,
        risk_metric: RiskMetric = RiskMetric.NOTIONAL,
        at: datetime,
    ) -> OrderState:
        if at.tzinfo is None:
            raise ValueError("plan timestamp must be timezone-aware")
        state = self.orders.plan(request)
        if reserve_currency is None and reserve_amount is None and margin_notional is None and risk_amount is None:
            return state
        if margin_notional is not None:
            if not reserve_currency:
                raise ValueError("reserve_currency is required for margin checks")
            if margin_notional <= 0:
                raise ValueError("margin_notional must be positive")
            if reserve_amount is not None:
                raise ValueError("reserve_amount and margin_notional cannot be supplied together")
        elif (reserve_currency is not None or reserve_amount is not None) and (
            not reserve_currency or reserve_amount is None
        ):
            raise ValueError("reserve_currency and reserve_amount must be supplied together")
        projection = derive_account_state(request.context, ledger=self.ledger, venue=venue_snapshot, holds=self.reservations)
        amount = reserve_amount
        if margin_notional is not None:
            amount = margin_notional / margin_leverage
        if amount is not None:
            reservation = Reservation(
                request.reservation_id or request.order_id,
                request.context.segment,
                reserve_currency,
                amount,
                "order margin pre-submit hold" if margin_notional is not None else "order pre-submit hold",
                at,
                order_id=request.order_id,
            )
        risk_reservation_id = request.reservation_id or request.order_id
        if self.risk is not None:
            selected_metric = RiskMetric.MARGIN if margin_notional is not None else risk_metric
            selected_amount = amount if amount is not None else risk_amount
            risk_request = RiskAssessmentRequest(
                request.order_id,
                (
                    RiskUsage(
                        selected_metric,
                        selected_amount or Decimal("0"),
                        (
                            BudgetRef("account", request.context.segment.value),
                            BudgetRef("instrument", str(request.instrument_id)),
                        ),
                    ),
                ),
                at,
            )
            assessment = self.risk.assess(risk_request)
            if assessment.decision is not RiskDecision.ALLOWED:
                reason = "; ".join(assessment.violations or ("order must be reduced",))
                self.orders.record(OrderEvent(request.order_id, OrderEventKind.REJECTED, at, reason=f"risk budget: {reason}"))
                return self.orders.get(request.order_id)
            try:
                self.risk.reserve(RiskReservationRequest(risk_reservation_id, risk_request))
            except ValueError as error:
                self.orders.record(OrderEvent(request.order_id, OrderEventKind.REJECTED, at, reason=f"risk budget: {error}"))
                return self.orders.get(request.order_id)
        if amount is None:
            return self.orders.record(OrderEvent(request.order_id, OrderEventKind.RESERVED, at))
        if margin_notional is None:
            check = reserve_asset_order(self.reservations, reservation, projection)
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
            self._release_risk(risk_reservation_id)
            self.orders.record(OrderEvent(request.order_id, OrderEventKind.REJECTED, at, reason=check.reason))
            return self.orders.get(request.order_id)
        return self.orders.record(OrderEvent(request.order_id, OrderEventKind.RESERVED, at))

    def submit_order(self, order_id: str, *, at: datetime) -> OrderState:
        if at.tzinfo is None:
            raise ValueError("submit timestamp must be timezone-aware")
        return self.orders.record(OrderEvent(order_id, OrderEventKind.SUBMITTED, at))

    def acknowledge_order(self, order_id: str, *, order_venue_id: str, at: datetime) -> OrderState:
        if at.tzinfo is None:
            raise ValueError("acknowledgement timestamp must be timezone-aware")
        if not order_venue_id.strip():
            raise ValueError("order venue id cannot be empty")
        return self.orders.record(OrderEvent(order_id, OrderEventKind.ACKNOWLEDGED, at, order_venue_id=order_venue_id))

    def mark_order_unknown(self, order_id: str, *, at: datetime, reason: str) -> OrderState:
        if at.tzinfo is None:
            raise ValueError("unknown-order timestamp must be timezone-aware")
        return self.orders.record(OrderEvent(order_id, OrderEventKind.UNKNOWN, at, reason=reason))

    def mark_reservation_reflected(self, order_id: str) -> None:
        state = self.orders.get(order_id)
        reservation_id = state.request.reservation_id or order_id
        self.reservations.reflect(reservation_id)

    def account_projection(
        self,
        context: AccountRuntimeContext,
        *,
        venue_snapshot: AccountSnapshot | None = None,
    ) -> AccountState:
        return derive_account_state(
            context,
            ledger=self.ledger,
            venue=venue_snapshot,
            holds=self.reservations,
        )

    def request_cancel(self, order_id: str, *, at: datetime) -> OrderState:
        return self.orders.record(OrderEvent(order_id, OrderEventKind.CANCEL_REQUESTED, at))

    def cancel_order(self, order_id: str, *, at: datetime) -> OrderState:
        return self.request_cancel(order_id, at=at)

    def cancel_confirmed(self, order_id: str, *, at: datetime) -> OrderState:
        state = self.orders.record(OrderEvent(order_id, OrderEventKind.CANCELED, at))
        self._release_reservation(state)
        return state

    def reject_order(self, order_id: str, *, at: datetime, reason: str = "") -> OrderState:
        state = self.orders.record(OrderEvent(order_id, OrderEventKind.REJECTED, at, reason=reason))
        self._release_reservation(state)
        return state

    def ingest_fill(self, report: FillReport) -> OrderState:
        state = self.orders.get(report.order_id)
        cumulative = report.cumulative_filled_quantity or state.filled_quantity + report.fill_quantity
        kind = OrderEventKind.FILLED if cumulative >= state.request.quantity else OrderEventKind.PARTIALLY_FILLED
        updated = self.orders.record(
            OrderEvent(report.order_id, kind, report.occurred_at, filled_quantity=cumulative)
        )
        self.ledger.record(
            AccountEvent(
                uuid4(),
                state.request.context.segment,
                AccountEventKind.FILL,
                report.occurred_at,
                report.settlement_currency,
                balance_delta=report.balance_delta,
                instrument_id=state.request.instrument_id,
                position_delta=report.fill_quantity * Decimal(state.request.side.position_sign),
                reference_id=report.order_id,
            )
        )
        if report.fee_amount:
            self.ledger.record(
                AccountEvent(
                    uuid4(),
                    state.request.context.segment,
                    AccountEventKind.FEE,
                    report.occurred_at,
                    report.fee_currency or report.settlement_currency,
                    balance_delta=-report.fee_amount,
                    reference_id=report.order_id,
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
                    state.request.order_id,
                    update.observed_at,
                    update.fill_quantity,
                    update.fill_price,
                    update.settlement_currency or _settlement_currency(state.request.instrument_id),
                    balance_delta=update.balance_delta
                    if update.balance_delta is not None
                    else _balance_delta(state.request.side, update.fill_quantity, update.fill_price),
                    fee_currency=update.fee_currency,
                    fee_amount=update.fee_amount,
                    cumulative_filled_quantity=update.filled_quantity,
                )
            )
        if update.kind is OrderEventKind.ACKNOWLEDGED and state.status is OrderStatus.ACKNOWLEDGED:
            return state
        updated = self.orders.record(
            OrderEvent(
                state.request.order_id,
                update.kind,
                update.observed_at,
                order_venue_id=update.order_venue_id or None,
                filled_quantity=update.filled_quantity
                if update.kind in {OrderEventKind.PARTIALLY_FILLED, OrderEventKind.FILLED}
                else None,
                reason=update.reason,
            )
        )
        if updated.status in {OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
            self._release_reservation(updated)
        return updated

    def _release_reservation(self, state: OrderState) -> None:
        reservation_id = state.request.reservation_id or state.request.order_id
        try:
            self.reservations.release(reservation_id)
        except KeyError:
            pass
        self._release_risk(reservation_id)

    def _consume_reservation(self, state: OrderState) -> None:
        reservation_id = state.request.reservation_id or state.request.order_id
        try:
            self.reservations.consume(reservation_id)
        except KeyError:
            pass
        self._consume_risk(reservation_id)

    def _release_risk(self, reservation_id: str) -> None:
        if self.risk is None:
            return
        try:
            self.risk.release(reservation_id)
        except (KeyError, ValueError):
            return

    def _consume_risk(self, reservation_id: str) -> None:
        if self.risk is None:
            return
        try:
            self.risk.consume(reservation_id)
        except (KeyError, ValueError):
            return

    def _import_execution_update(self, update: ExecutionUpdate) -> OrderState:
        if update.kind not in {OrderEventKind.ACKNOWLEDGED, OrderEventKind.PARTIALLY_FILLED}:
            raise LookupError(f"terminal execution update has no known local order: {update.order_venue_id}")
        if update.context is None:
            raise LookupError(f"execution update has no account context for unknown order: {update.order_venue_id}")
        if update.instrument_id is None or update.side is None or update.quantity is None or update.order_type is None:
            raise ValueError("execution update cannot import an unknown order without order identity fields")
        return self.orders.import_order_venue_open_order(
            context=update.context,
            order_venue_id=update.order_venue_id,
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


def asset_order_request(
    *,
    order_id: str | None = None,
    context: AccountRuntimeContext,
    instrument_id: InstrumentId | str,
    side: OrderSide,
    quantity: Decimal,
    order_type: OrderType = OrderType.MARKET,
    limit_price: Decimal | None = None,
) -> OrderRequest:
    if order_id is None:
        raise ValueError("order_id is required")
    return OrderRequest(order_id, context, instrument_id, side, quantity, order_type, limit_price)


def _known_order(orders: OrderJournal, update: ExecutionUpdate) -> OrderState | None:
    if update.order_id:
        try:
            return orders.get(update.order_id)
        except LookupError:
            pass
    if update.order_venue_id:
        try:
            return orders.get_by_order_venue_id(update.order_venue_id)
        except LookupError:
            pass
    return None


def _settlement_currency(symbol: object) -> str:
    symbol = str(symbol)
    if "/" not in symbol:
        parts = symbol.split(":")
        if len(parts) >= 4 and parts[0] == "instrument":
            return parts[-1].upper() or "USD"
        return "USD"
    return symbol.split("/", 1)[1].split(":", 1)[0] or "USD"


def _balance_delta(side: OrderSide, quantity: Decimal, price: Decimal) -> Decimal:
    cost = quantity * price
    return cost if side is OrderSide.SELL else -cost


__all__ = ["ExecutionCoordinator", "FillReport", "asset_order_request"]
