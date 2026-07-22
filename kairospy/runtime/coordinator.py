from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5
from kairospy.execution.ports import ComboOrderRequest, OrderAck, OrderRequest
from kairospy.runtime.clock import Clock, SystemClock
from kairospy.strategy.intents import CancelIntent
from kairospy.identity import AccountRef
from kairospy.execution.router import ExecutionRouter
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.infrastructure.storage.codec import from_primitive

if TYPE_CHECKING:
    from kairospy.runtime.application import KairosApplication

from kairospy.governance.kill_switch import KillSwitch
from kairospy.governance.reconciliation import ReconciliationService
from kairospy.runtime.store.event_log import PersistentEventLog
from kairospy.runtime.store.runtime_store import SQLiteRuntimeStore
from kairospy.runtime.testing.faults import RuntimeFaultInjector, RuntimeFaultPoint, inject


@dataclass(frozen=True, slots=True)
class PersistedOrderRecord:
    request: OrderRequest
    ack: OrderAck


@dataclass(frozen=True, slots=True)
class PersistedComboOrderRecord:
    request: ComboOrderRequest
    ack: OrderAck


@dataclass(frozen=True, slots=True)
class PersistedCancellationRecord:
    intent: CancelIntent
    account: AccountRef
    venue_order_id: str


@dataclass(frozen=True, slots=True)
class StrategyCancellationResult:
    strategy_id: str
    cancelled_client_order_ids: tuple[str, ...]
    failures: tuple[tuple[str, str], ...] = ()


class ExecutionCoordinator:
    def __init__(self, router: ExecutionRouter, reconciliation: dict[AccountRef, ReconciliationService],
                 kill_switch: KillSwitch, event_log: PersistentEventLog, clock: Clock | None = None,
                 runtime_store: SQLiteRuntimeStore | None = None,
                 application: "KairosApplication | None" = None,
                 fault_injector: RuntimeFaultInjector | None = None) -> None:
        if application is None:
            raise ValueError("ExecutionCoordinator requires the authoritative KairosApplication")
        self.router, self.reconciliation, self.kill_switch, self.event_log = router, reconciliation, kill_switch, event_log
        self.clock = clock or SystemClock()
        self.runtime_store = runtime_store
        self.application = application
        self.fault_injector = fault_injector

    def activate(self) -> None:
        """Activate the formal coordinator after KairosApplication reached READY."""
        self.application.require_operational()

    def submit(self, request: OrderRequest, at):
        self.application.require_operational()
        if self.application.status.value == "reduce_only" and not request.instructions.reduce_only:
            raise RuntimeError("runtime is reduce-only: non-reducing orders are blocked")
        if self.kill_switch.triggered and not request.instructions.reduce_only:
            raise RuntimeError("kill switch active: only reduce-only orders are allowed")
        if request.instructions.reduce_only:
            service = self.reconciliation.get(request.account)
            if service is None:
                raise RuntimeError("reduce-only validation requires an account gateway")
            state = service.account_gateway.account_state(request.account)
            current = dict(state.positions).get(request.instrument_id, 0)
            projected = current + request.quantity * request.side.sign
            if current == 0 or current * projected < 0 or abs(projected) >= abs(current):
                raise ValueError("reduce-only order does not strictly reduce the current position")
        if self.runtime_store is not None:
            durable = self.runtime_store.order(request.client_order_id)
            if durable is not None:
                if durable.request != request:
                    raise ValueError("client order id was already used for a different request")
                if durable.ack is not None:
                    return durable.ack
                if durable.status in {
                    DurableOrderStatus.SUBMITTING,
                    DurableOrderStatus.UNKNOWN,
                    DurableOrderStatus.CANCELLING,
                }:
                    raise RuntimeError(
                        f"order {request.client_order_id} requires venue recovery from {durable.status.value}"
                    )
                if durable.status.terminal:
                    raise RuntimeError(
                        f"order {request.client_order_id} is already terminal with status {durable.status.value}"
                    )
            else:
                durable = self.runtime_store.create_order(request, at)
            if durable.status is DurableOrderStatus.PLANNED:
                durable = self.runtime_store.transition_order(
                    request.client_order_id, DurableOrderStatus.APPROVED, at,
                )
            if durable.status is DurableOrderStatus.APPROVED:
                self.runtime_store.transition_order(
                    request.client_order_id, DurableOrderStatus.SUBMITTING, at,
                )
            inject(
                self.fault_injector, RuntimeFaultPoint.AFTER_ORDER_SUBMITTING_BEFORE_VENUE,
                client_order_id=request.client_order_id,
            )
        existing = self.event_log.find(f"order:{request.client_order_id}")
        if existing is not None:
            record = from_primitive(existing["payload"], PersistedOrderRecord)
            if record.request != request:
                raise ValueError("client order id was already used for a different request")
            return record.ack
        if self.event_log.find(f"combo:{request.client_order_id}") is not None:
            raise ValueError("client order id was already used for a combo order")
        try:
            ack = self.router.submit(request, at)
        except ValueError as error:
            if self.runtime_store is not None:
                self.runtime_store.transition_order(
                    request.client_order_id, DurableOrderStatus.REJECTED, self.clock.now(), reason=str(error),
                )
            raise
        except Exception as error:
            if self.runtime_store is not None:
                self.runtime_store.transition_order(
                    request.client_order_id, DurableOrderStatus.UNKNOWN, self.clock.now(), reason=str(error),
                )
            raise
        inject(
            self.fault_injector, RuntimeFaultPoint.AFTER_VENUE_ACCEPT_BEFORE_ACK_PERSIST,
            client_order_id=request.client_order_id, venue_order_id=ack.venue_order_id,
        )
        if self.runtime_store is not None:
            self.runtime_store.transition_order(
                request.client_order_id, DurableOrderStatus.ACKNOWLEDGED, ack.accepted_at, ack=ack,
            )
        self.event_log.append(f"order:{request.client_order_id}", "order_ack", PersistedOrderRecord(request, ack))
        return ack

    def submit_combo(self, request: ComboOrderRequest, at):
        self.application.require_operational()
        if self.application.status.value == "reduce_only" and not request.instructions.reduce_only:
            raise RuntimeError("runtime is reduce-only: non-reducing combo orders are blocked")
        if self.kill_switch.triggered and not request.instructions.reduce_only:
            raise RuntimeError("kill switch active: only reduce-only orders are allowed")
        if request.instructions.reduce_only:
            self._validate_combo_reduce_only(request)
        if self.runtime_store is not None:
            durable = self.runtime_store.order(request.client_order_id)
            if durable is not None:
                if durable.request != request:
                    raise ValueError("client order id was already used for a different combo request")
                if durable.ack is not None:
                    return durable.ack
                if durable.status in {
                    DurableOrderStatus.SUBMITTING,
                    DurableOrderStatus.UNKNOWN,
                    DurableOrderStatus.CANCELLING,
                }:
                    raise RuntimeError(
                        f"combo order {request.client_order_id} requires venue recovery from {durable.status.value}"
                    )
                if durable.status.terminal:
                    raise RuntimeError(
                        f"combo order {request.client_order_id} is already terminal with status {durable.status.value}"
                    )
            else:
                durable = self.runtime_store.create_order(request, at)
            if durable.status is DurableOrderStatus.PLANNED:
                durable = self.runtime_store.transition_order(
                    request.client_order_id, DurableOrderStatus.APPROVED, at,
                )
            if durable.status is DurableOrderStatus.APPROVED:
                self.runtime_store.transition_order(
                    request.client_order_id, DurableOrderStatus.SUBMITTING, at,
                )
        event_id = f"combo:{request.client_order_id}"
        existing = self.event_log.find(event_id)
        if existing is not None:
            record = from_primitive(existing["payload"], PersistedComboOrderRecord)
            if record.request != request:
                raise ValueError("client order id was already used for a different combo request")
            return record.ack
        if self.event_log.find(f"order:{request.client_order_id}") is not None:
            raise ValueError("client order id was already used for a single order")
        try:
            ack = self.router.submit_combo(request, at)
        except ValueError as error:
            if self.runtime_store is not None:
                self.runtime_store.transition_order(
                    request.client_order_id, DurableOrderStatus.REJECTED, self.clock.now(), reason=str(error),
                )
            raise
        except Exception as error:
            if self.runtime_store is not None:
                self.runtime_store.transition_order(
                    request.client_order_id, DurableOrderStatus.UNKNOWN, self.clock.now(), reason=str(error),
                )
            raise
        if self.runtime_store is not None:
            self.runtime_store.transition_order(
                request.client_order_id, DurableOrderStatus.ACKNOWLEDGED, ack.accepted_at, ack=ack,
            )
        self.event_log.append(event_id, "combo_order_ack", PersistedComboOrderRecord(request, ack))
        return ack

    def cancel(self, intent: CancelIntent, account: AccountRef) -> None:
        self.application.require_operational()
        cancellation_id = f"cancel:{intent.intent_id}"
        if self.event_log.find(cancellation_id) is not None:
            return
        durable = self.runtime_store.order(intent.client_order_id) if self.runtime_store is not None else None
        if durable is not None and durable.status is DurableOrderStatus.CANCELLED:
            return
        existing = self.event_log.find(f"order:{intent.client_order_id}") or self.event_log.find(f"combo:{intent.client_order_id}")
        if durable is None and existing is None:
            raise LookupError(f"working order not found for client id {intent.client_order_id}")
        if durable is not None:
            request, ack = durable.request, durable.ack
            if ack is None:
                raise RuntimeError("cannot cancel an order without a recovered venue acknowledgement")
        else:
            assert existing is not None
            record_type = PersistedComboOrderRecord if existing["event_type"] == "combo_order_ack" else PersistedOrderRecord
            record = from_primitive(existing["payload"], record_type)
            request, ack = record.request, record.ack
        if request.account != account:
            raise ValueError("cancel account does not match original order")
        if self.runtime_store is not None:
            self.runtime_store.transition_order(
                intent.client_order_id, DurableOrderStatus.CANCELLING, self.clock.now(), reason=intent.reason,
            )
        try:
            self.router.cancel(account, ack.venue_order_id)
        except Exception as error:
            if self.runtime_store is not None:
                self.runtime_store.transition_order(
                    intent.client_order_id, DurableOrderStatus.UNKNOWN, self.clock.now(), reason=str(error),
                )
            raise
        if self.runtime_store is not None:
            self.runtime_store.transition_order(
                intent.client_order_id, DurableOrderStatus.CANCELLED, self.clock.now(), reason=intent.reason,
            )
        self.event_log.append(
            cancellation_id, "order_cancelled",
            PersistedCancellationRecord(intent, account, ack.venue_order_id),
        )

    def cancel_strategy_orders(
        self,
        strategy_id: str,
        account: AccountRef,
        reason: str,
    ) -> StrategyCancellationResult:
        self.application.require_operational()
        if self.runtime_store is None:
            raise RuntimeError("strategy-scoped cancellation requires a runtime store")
        if not strategy_id.strip() or not reason.strip():
            raise ValueError("strategy-scoped cancellation requires strategy_id and reason")
        cancelled: list[str] = []
        failures: list[tuple[str, str]] = []
        records = self.runtime_store.working_orders(strategy_id=strategy_id, account=account)
        for record in records:
            intent = CancelIntent(
                uuid5(NAMESPACE_URL, f"kairospy:cancel-strategy-order:{strategy_id}:{record.request.client_order_id}"),
                strategy_id,
                record.request.client_order_id,
                reason,
            )
            try:
                self.cancel(intent, account)
            except Exception as error:
                failures.append((record.request.client_order_id, str(error)))
            else:
                cancelled.append(record.request.client_order_id)
        return StrategyCancellationResult(strategy_id, tuple(cancelled), tuple(failures))

    def _validate_combo_reduce_only(self, request: ComboOrderRequest) -> None:
        service = self.reconciliation.get(request.account)
        if service is None:
            raise RuntimeError("reduce-only validation requires an account gateway")
        positions = dict(service.account_gateway.account_state(request.account).positions)
        for leg in request.legs:
            current = positions.get(leg.instrument_id, 0)
            projected = current + request.quantity * leg.ratio * leg.side.sign
            if current == 0 or current * projected < 0 or abs(projected) >= abs(current):
                raise ValueError("reduce-only combo does not strictly reduce every leg")
