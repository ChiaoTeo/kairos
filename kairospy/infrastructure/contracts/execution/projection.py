"""Application projection over Execution v2 generated view roots."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from kairospy.application.execution.models import (
    CommitmentStatus,
    ExecutionIntent,
    IntentStatus,
    Order,
    OrderSide,
    OrderStatus,
    OrderCommitment,
    RiskReservationSaga,
    RiskReservationSagaStatus,
)
from kairospy.application.reference import InstrumentRef
from kairospy.application.workspace import InstanceWorkspace
from kairospy.domain_types import AccountId, InstrumentId, IntentId, OrderId, SegmentKey

from .view import ExecutionViewKey, ExecutionViewKind, ExecutionViewReader


class ExecutionProjection:
    """Read-only application projection backed only by v2 active views."""

    def __init__(self, instance: InstanceWorkspace, *, retries: int = 8) -> None:
        self._root = instance.root
        self._workspace_id = instance.workspace.workspace_id
        self._launch_id = instance.launch_id
        self._instance_id = instance.instance_id
        self._retries = retries

    def orders(self) -> tuple[Order, ...]:
        value = self._read(ExecutionViewKind.ACTIVE_ORDERS)
        return tuple(_order(value.Orders(index)) for index in range(value.OrdersLength()))

    def get_order(self, order_id: str) -> Order | None:
        return next((value for value in self.orders() if str(value.id) == order_id), None)

    def commitments(self) -> tuple[OrderCommitment, ...]:
        value = self._read(ExecutionViewKind.ACTIVE_ORDERS)
        return tuple(
            _commitment(value.Commitments(index))
            for index in range(value.CommitmentsLength())
        )

    def risk_reservations(self) -> tuple[RiskReservationSaga, ...]:
        value = self._read(ExecutionViewKind.ACTIVE_ORDERS)
        return tuple(
            _risk_reservation(value.RiskReservations(index))
            for index in range(value.RiskReservationsLength())
        )

    def open_orders(self, *, account_id: str | None = None) -> tuple[Order, ...]:
        terminal = {
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
        }
        return tuple(
            value
            for value in self.orders()
            if value.status not in terminal
            and (account_id is None or str(value.account_id) == account_id)
        )

    def intents(self) -> tuple[ExecutionIntent, ...]:
        value = self._read(ExecutionViewKind.ACTIVE_INTENTS)
        return tuple(_intent(value.Intents(index)) for index in range(value.IntentsLength()))

    def get_intent(self, intent_id: str) -> ExecutionIntent | None:
        return next((value for value in self.intents() if str(value.id) == intent_id), None)

    def _read(self, kind: ExecutionViewKind) -> Any:
        key = ExecutionViewKey(
            workspace_id=self._workspace_id,
            launch_id=self._launch_id,
            instance_id=self._instance_id,
            kind=kind,
        )
        return ExecutionViewReader(self._root, key, retries=self._retries).read().value


def _order(value: Any) -> Order:
    instrument_id = _required_text(value.InstrumentId(), "order instrument_id")
    status = _order_status(int(value.Lifecycle()))
    return Order(
        id=OrderId(_required_text(value.OrderId(), "order_id")),
        strategy_id=_required_text(value.StrategyId(), "order strategy_id"),
        intent_id=_optional_id(value.IntentId(), IntentId),
        instrument=InstrumentRef(InstrumentId(instrument_id), instrument_id.rsplit(":", 1)[-1]),
        account_id=AccountId(_required_text(value.AccountId(), "order account_id")),
        side=OrderSide.BUY if int(value.Side()) == 1 else OrderSide.SELL,
        quantity=_required_decimal(value.Quantity(), "order quantity"),
        filled_quantity=_required_decimal(value.FilledQuantity(), "order filled_quantity"),
        limit_price=_decimal(value.LimitPrice()),
        status=status,
        updated_at=None,
    )


def _intent(value: Any) -> ExecutionIntent:
    intent = value.Intent()
    if intent is None:
        raise ValueError("Execution intent state payload is missing")
    instrument_id = _required_text(intent.Legs(0).InstrumentId(), "intent instrument_id")
    accounts = tuple(
        AccountId(_required_text(intent.Legs(index).AccountId(), "intent account_id"))
        for index in range(intent.LegsLength())
    )


def _commitment(value: Any) -> OrderCommitment:
    return OrderCommitment(
        order_id=OrderId(_required_text(value.OrderId(), "commitment order_id")),
        account_id=AccountId(_required_text(value.AccountId(), "commitment account_id")),
        segment_key=SegmentKey(
            _required_text(value.SegmentKey(), "commitment segment_key")
        ),
        instrument_id=InstrumentId(
            _required_text(value.InstrumentId(), "commitment instrument_id")
        ),
        resource_kind={1: "asset", 2: "instrument", 3: "margin_notional"}.get(
            int(value.ResourceKind()), "unspecified"
        ),
        resource_id=_required_text(value.ResourceId(), "commitment resource_id"),
        amount=_required_decimal(value.Amount(), "commitment amount"),
        remaining_quantity=_required_decimal(
            value.RemainingQuantity(), "commitment remaining_quantity"
        ),
        status={
            1: CommitmentStatus.HELD_BEFORE_SEND,
            2: CommitmentStatus.ACTIVE,
            3: CommitmentStatus.UNCERTAIN,
            4: CommitmentStatus.REDUCED,
            5: CommitmentStatus.RELEASED,
            6: CommitmentStatus.RECONCILED,
        }.get(int(value.Lifecycle()), CommitmentStatus.UNCERTAIN),
        basis_kind={
            1: "quote_price_cap",
            2: "base_quantity",
            3: "contract_notional",
            4: "simulation_quantity",
        }.get(int(value.BasisKind()), "unspecified"),
        updated_at_unix_nanos=int(value.UpdatedAtUnixNanos()),
    )


def _risk_reservation(value: Any) -> RiskReservationSaga:
    return RiskReservationSaga(
        order_id=OrderId(_required_text(value.OrderId(), "reservation order_id")),
        reservation_id=_required_text(
            value.ReservationId(), "reservation reservation_id"
        ),
        idempotency_key=_required_text(
            value.IdempotencyKey(), "reservation idempotency_key"
        ),
        account_id=AccountId(
            _required_text(value.AccountId(), "reservation account_id")
        ),
        amount=_required_decimal(value.Amount(), "reservation amount"),
        status={
            1: RiskReservationSagaStatus.AUTHORIZE_PENDING,
            2: RiskReservationSagaStatus.ACTIVE,
            3: RiskReservationSagaStatus.RESIZE_PENDING,
            4: RiskReservationSagaStatus.RELEASE_PENDING,
            5: RiskReservationSagaStatus.CONSUME_PENDING,
            6: RiskReservationSagaStatus.RELEASED,
            7: RiskReservationSagaStatus.CONSUMED,
            8: RiskReservationSagaStatus.EXPIRED,
            9: RiskReservationSagaStatus.UNCERTAIN,
        }.get(int(value.Lifecycle()), RiskReservationSagaStatus.UNCERTAIN),
        risk_generation=int(value.RiskGeneration()),
        risk_event_sequence=int(value.RiskEventSequence()),
        policy_version=int(value.PolicyVersion()),
        expires_at_unix_nanos=int(value.ExpiresAtUnixNanos()),
        updated_at_unix_nanos=int(value.UpdatedAtUnixNanos()),
    )
    order_ids: tuple[OrderId, ...] = ()
    return ExecutionIntent(
        id=IntentId(_required_text(intent.IntentId(), "intent_id")),
        strategy_id=_required_text(intent.StrategyId(), "intent strategy_id"),
        instrument=InstrumentRef(InstrumentId(instrument_id), instrument_id.rsplit(":", 1)[-1]),
        account_ids=accounts,
        target_quantity=None,
        status=_intent_status(int(value.Lifecycle())),
        reason=_text(value.Reason()) or "",
        order_ids=order_ids,
    )


def _order_status(value: int) -> OrderStatus:
    return {
        1: OrderStatus.PENDING,
        2: OrderStatus.SUBMITTING,
        3: OrderStatus.ACCEPTED,
        4: OrderStatus.PARTIALLY_FILLED,
        5: OrderStatus.FILLED,
        6: OrderStatus.CANCEL_REQUESTED,
        7: OrderStatus.CANCELED,
        8: OrderStatus.REJECTED,
        9: OrderStatus.EXPIRED,
        11: OrderStatus.FAILED,
    }.get(value, OrderStatus.UNKNOWN)


def _intent_status(value: int) -> IntentStatus:
    names = {
        1: IntentStatus.ACCEPTED,
        2: IntentStatus.PLANNING,
        3: IntentStatus.PLANNED,
        4: IntentStatus.EXECUTING,
        5: IntentStatus.PARTIALLY_FILLED,
        6: IntentStatus.CANCEL_REQUESTED,
        7: IntentStatus.COMPLETED,
        8: IntentStatus.REJECTED,
        9: IntentStatus.CANCELED,
        10: IntentStatus.EXPIRED,
        11: IntentStatus.FAILED,
        12: IntentStatus.COMPENSATING,
        13: IntentStatus.RECONCILIATION_REQUIRED,
    }
    return names.get(value, IntentStatus.UNKNOWN)


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    raw = value
    return Decimal(int(raw.Mantissa())).scaleb(-int(raw.Scale()))  # type: ignore[attr-defined]


def _required_decimal(value: object | None, name: str) -> Decimal:
    result = _decimal(value)
    if result is None:
        raise ValueError(f"{name} is required")
    return result


def _text(value: bytes | None) -> str | None:
    return None if value is None else value.decode()


def _required_text(value: bytes | None, name: str) -> str:
    result = _text(value)
    if result is None or not result.strip():
        raise ValueError(f"{name} is required")
    return result


def _optional_id(value: bytes | None, kind: type) -> Any:
    raw = _text(value)
    return None if raw is None or not raw.strip() else kind(raw)


__all__ = ["ExecutionProjection"]
