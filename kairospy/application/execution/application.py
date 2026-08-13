from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import TYPE_CHECKING

from kairospy.application.reference import InstrumentRef
from kairospy.domain_types import AccountId, InstrumentId, IntentId, OrderId

from .intents import TargetPositionRequest
from .models import (
    BulkOrderCommandReceipt,
    DeliveryCertainty,
    ExecutionIntent,
    IntentReceipt,
    Order,
    OrderCommandReceipt,
    OrderRequest,
    ReplaceOrderRequest,
    SubmissionStatus,
)

if TYPE_CHECKING:
    from kairospy.infrastructure.contracts.execution import ExecutionMmapProjection
    from kairospy.infrastructure.transport.commands import ExecutionCommandClient
    from kairospy.strategy.results import CommandResult


class ExecutionApplication:
    """Concrete strategy-facing Execution use cases and projections."""

    def __init__(
        self,
        commands: ExecutionCommandClient | None,
        projection: ExecutionMmapProjection | None,
        *,
        strategy_id: str,
        instance_id: str,
        disabled_reason: str = "execution is disabled for this launch",
    ) -> None:
        if not strategy_id.strip() or not instance_id.strip():
            raise ValueError("strategy_id and instance_id are required")
        self._commands = commands
        self._projection = projection
        self._strategy_id = strategy_id
        self._instance_id = instance_id
        self._disabled_reason = disabled_reason
        self._event_sequence: int | None = None
        self._event_time_unix_nanos: int | None = None
        self._request_counter = 0

    def bind_event(
        self,
        sequence: int | None,
        occurred_at_unix_nanos: int | None,
    ) -> None:
        """Bind command causation to the currently dispatched strategy event."""

        self._event_sequence = sequence
        self._event_time_unix_nanos = occurred_at_unix_nanos

    def target_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        quantity: Decimal,
        *,
        account: AccountId | str,
        limit_price: Decimal | None = None,
        reason: str = "",
        intent_id: IntentId | None = None,
    ) -> IntentReceipt:
        request_id = self._request_id("intent.target_position")
        if self._commands is None:
            return self._rejected_intent(request_id)
        request = TargetPositionRequest(
            instrument_id=str(_instrument_id(instrument)),
            quantity=quantity,
            account_id=str(account),
            limit_price=limit_price,
            reason=reason,
            intent_id=None if intent_id is None else str(intent_id),
            source_event_sequence=self._event_sequence,
            source_event_time_unix_nanos=self._event_time_unix_nanos,
        )
        return _intent_receipt(
            self._commands.target_position(request, **self._identity(request_id))
        )

    def close_position(
        self,
        instrument: InstrumentRef | InstrumentId,
        *,
        account: AccountId | str,
        reason: str = "",
        intent_id: IntentId | None = None,
    ) -> IntentReceipt:
        return self.target_position(
            instrument,
            Decimal("0"),
            account=account,
            reason=reason,
            intent_id=intent_id,
        )

    def cancel_intent(self, intent_id: IntentId, *, reason: str = "") -> IntentReceipt:
        request_id = self._request_id("intent.cancel")
        if self._commands is None:
            return self._rejected_intent(request_id)
        return _intent_receipt(
            self._commands.cancel_intent(
                str(intent_id), reason=reason, **self._identity(request_id)
            )
        )

    def submit_order(self, request: OrderRequest) -> OrderCommandReceipt:
        request_id = self._request_id("execution.submit_order")
        if self._commands is None:
            return self._rejected_order(request_id)
        return _order_receipt(
            self._commands.submit_order(request, **self._identity(request_id))
        )

    def cancel_order(
        self, order_id: OrderId, *, reason: str = ""
    ) -> OrderCommandReceipt:
        request_id = self._request_id("execution.cancel_order")
        if self._commands is None:
            return self._rejected_order(request_id)
        return _order_receipt(
            self._commands.cancel_order(
                str(order_id), reason=reason, **self._identity(request_id)
            )
        )

    def replace_order(
        self, order_id: OrderId, request: ReplaceOrderRequest
    ) -> OrderCommandReceipt:
        request_id = self._request_id("execution.replace_order")
        if self._commands is None:
            return self._rejected_order(request_id)
        return _order_receipt(
            self._commands.replace_order(
                str(order_id), request, **self._identity(request_id)
            )
        )

    def cancel_all(
        self,
        *,
        instrument: InstrumentRef | InstrumentId | None = None,
        account: AccountId | str | None = None,
        reason: str = "",
    ) -> BulkOrderCommandReceipt:
        if instrument is None and account is None:
            raise ValueError("cancel_all requires an instrument or account scope")
        request_id = self._request_id("execution.cancel_all")
        if self._commands is None:
            return BulkOrderCommandReceipt(
                request_id,
                (),
                SubmissionStatus.REJECTED,
                DeliveryCertainty.NOT_SENT,
                self._disabled_reason,
            )
        result = self._commands.cancel_all(
            instrument_id=None
            if instrument is None
            else str(_instrument_id(instrument)),
            account_id=None if account is None else str(account),
            reason=reason,
            **self._identity(request_id),
        )
        return _bulk_receipt(result)

    def intent(self, intent_id: IntentId) -> ExecutionIntent | None:
        if self._projection is None:
            return None
        return self._projection.get_intent(str(intent_id))

    def order(self, order_id: OrderId) -> Order | None:
        if self._projection is None:
            return None
        return self._projection.get_order(str(order_id))

    def open_orders(
        self,
        *,
        instrument: InstrumentRef | InstrumentId | None = None,
        account: AccountId | str | None = None,
    ) -> tuple[Order, ...]:
        if self._projection is None:
            return ()
        orders = self._projection.open_orders(
            account_id=None if account is None else str(account)
        )
        instrument_id = None if instrument is None else _instrument_id(instrument)
        return (
            orders
            if instrument_id is None
            else tuple(
                order for order in orders if order.instrument.id == instrument_id
            )
        )

    def _request_id(self, operation: str) -> str:
        self._request_counter += 1
        return (
            f"{self._strategy_id}:{self._instance_id}:{operation}:"
            f"{self._event_sequence or 0}:{self._request_counter}"
        )

    def _identity(self, request_id: str) -> dict[str, str]:
        return {
            "strategy_id": self._strategy_id,
            "instance_id": self._instance_id,
            "request_id": request_id,
        }

    def _rejected_intent(self, request_id: str) -> IntentReceipt:
        return IntentReceipt(
            request_id,
            None,
            SubmissionStatus.REJECTED,
            DeliveryCertainty.NOT_SENT,
            self._disabled_reason,
        )

    def _rejected_order(self, request_id: str) -> OrderCommandReceipt:
        return OrderCommandReceipt(
            request_id,
            None,
            None,
            SubmissionStatus.REJECTED,
            DeliveryCertainty.NOT_SENT,
            self._disabled_reason,
        )


def _instrument_id(value: InstrumentRef | InstrumentId) -> InstrumentId:
    return value.id if isinstance(value, InstrumentRef) else value


def _submission_status(value: CommandResult) -> SubmissionStatus:
    status = value.status.lower()
    return (
        SubmissionStatus(status)
        if status in SubmissionStatus._value2member_map_
        else SubmissionStatus.REJECTED
    )


def _certainty(value: CommandResult) -> DeliveryCertainty:
    return (
        DeliveryCertainty.NOT_SENT
        if _submission_status(value) is SubmissionStatus.REJECTED
        else DeliveryCertainty.SENT
    )


def _result_id(value: CommandResult, name: str, id_type):
    raw = value.result.get(name)
    if raw is None:
        nested = value.result.get("result")
        raw = nested.get(name) if isinstance(nested, Mapping) else None
    return id_type(raw) if isinstance(raw, str) and raw.strip() else None


def _intent_receipt(value: CommandResult) -> IntentReceipt:
    return IntentReceipt(
        request_id=value.request_id,
        intent_id=_result_id(value, "intent_id", IntentId),
        status=_submission_status(value),
        delivery_certainty=_certainty(value),
        error=value.error,
    )


def _order_receipt(value: CommandResult) -> OrderCommandReceipt:
    return OrderCommandReceipt(
        request_id=value.request_id,
        order_id=_result_id(value, "order_id", OrderId),
        intent_id=_result_id(value, "intent_id", IntentId),
        status=_submission_status(value),
        delivery_certainty=_certainty(value),
        error=value.error,
    )


def _bulk_receipt(value: CommandResult) -> BulkOrderCommandReceipt:
    raw_ids = value.result.get("order_ids", ())
    if not isinstance(raw_ids, (list, tuple)):
        raw_ids = ()
    return BulkOrderCommandReceipt(
        request_id=value.request_id,
        order_ids=tuple(OrderId(item) for item in raw_ids if isinstance(item, str)),
        status=_submission_status(value),
        delivery_certainty=_certainty(value),
        error=value.error,
    )
