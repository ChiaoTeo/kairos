from __future__ import annotations

from dataclasses import dataclass

from trading.adapters.base import ComboOrderRequest, OrderAck, OrderRequest
from trading.domain.intent import CancelIntent
from trading.domain.identity import AccountKey
from trading.execution.router import ExecutionRouter
from trading.storage.codec import from_primitive

from .event_log import PersistentEventLog
from .kill_switch import KillSwitch
from .readiness import Component, SystemReadiness
from .reconciliation import ReconciliationService


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
    account: AccountKey
    venue_order_id: str


class TradingCoordinator:
    def __init__(self, router: ExecutionRouter, reconciliation: dict[AccountKey, ReconciliationService], kill_switch: KillSwitch, event_log: PersistentEventLog) -> None:
        self.router, self.reconciliation, self.kill_switch, self.event_log = router, reconciliation, kill_switch, event_log
        self.readiness = SystemReadiness()

    def start(self, accounts: tuple[AccountKey, ...], *, catalog_ready: bool, market_data_ready: bool, execution_ready: bool) -> None:
        self.readiness.update(Component.CATALOG, catalog_ready, "ok" if catalog_ready else "catalog unavailable")
        self.readiness.update(Component.MARKET_DATA, market_data_ready, "ok" if market_data_ready else "market data unavailable")
        self.readiness.update(Component.EXECUTION, execution_ready, "ok" if execution_ready else "execution unavailable")
        reports = [self.reconciliation[account].reconcile(account) for account in accounts]
        matched = all(report.matched for report in reports)
        self.readiness.update(Component.ACCOUNT, True, "account adapters responding")
        self.readiness.update(Component.RECONCILIATION, matched, "matched" if matched else "ledger differs from venue")
        for report in reports:
            self.event_log.append(f"reconcile:{report.account.value}:{report.checked_at.isoformat()}", "reconciliation", report)
        self.readiness.require_ready()

    def submit(self, request: OrderRequest, at):
        self.readiness.require_ready()
        if self.kill_switch.triggered and not request.instructions.reduce_only:
            raise RuntimeError("kill switch active: only reduce-only orders are allowed")
        if request.instructions.reduce_only:
            service = self.reconciliation.get(request.account)
            if service is None:
                raise RuntimeError("reduce-only validation requires an account adapter")
            state = service.account_adapter.account_state(request.account)
            current = dict(state.positions).get(request.instrument_id, 0)
            projected = current + request.quantity * request.side.sign
            if current == 0 or current * projected < 0 or abs(projected) >= abs(current):
                raise ValueError("reduce-only order does not strictly reduce the current position")
        existing = self.event_log.find(f"order:{request.client_order_id}")
        if existing is not None:
            record = from_primitive(existing["payload"], PersistedOrderRecord)
            if record.request != request:
                raise ValueError("client order id was already used for a different request")
            return record.ack
        if self.event_log.find(f"combo:{request.client_order_id}") is not None:
            raise ValueError("client order id was already used for a combo order")
        ack = self.router.submit(request, at)
        self.event_log.append(f"order:{request.client_order_id}", "order_ack", PersistedOrderRecord(request, ack))
        return ack

    def submit_combo(self, request: ComboOrderRequest, at):
        self.readiness.require_ready()
        if self.kill_switch.triggered and not request.instructions.reduce_only:
            raise RuntimeError("kill switch active: only reduce-only orders are allowed")
        if request.instructions.reduce_only:
            self._validate_combo_reduce_only(request)
        event_id = f"combo:{request.client_order_id}"
        existing = self.event_log.find(event_id)
        if existing is not None:
            record = from_primitive(existing["payload"], PersistedComboOrderRecord)
            if record.request != request:
                raise ValueError("client order id was already used for a different combo request")
            return record.ack
        if self.event_log.find(f"order:{request.client_order_id}") is not None:
            raise ValueError("client order id was already used for a single order")
        ack = self.router.submit_combo(request, at)
        self.event_log.append(event_id, "combo_order_ack", PersistedComboOrderRecord(request, ack))
        return ack

    def cancel(self, intent: CancelIntent, account: AccountKey) -> None:
        self.readiness.require_ready()
        cancellation_id = f"cancel:{intent.intent_id}"
        if self.event_log.find(cancellation_id) is not None:
            return
        existing = self.event_log.find(f"order:{intent.client_order_id}") or self.event_log.find(f"combo:{intent.client_order_id}")
        if existing is None:
            raise LookupError(f"working order not found for client id {intent.client_order_id}")
        record_type = PersistedComboOrderRecord if existing["event_type"] == "combo_order_ack" else PersistedOrderRecord
        record = from_primitive(existing["payload"], record_type)
        if record.request.account != account:
            raise ValueError("cancel account does not match original order")
        self.router.cancel(account, record.ack.venue_order_id)
        self.event_log.append(
            cancellation_id, "order_cancelled",
            PersistedCancellationRecord(intent, account, record.ack.venue_order_id),
        )

    def _validate_combo_reduce_only(self, request: ComboOrderRequest) -> None:
        service = self.reconciliation.get(request.account)
        if service is None:
            raise RuntimeError("reduce-only validation requires an account adapter")
        positions = dict(service.account_adapter.account_state(request.account).positions)
        for leg in request.legs:
            current = positions.get(leg.instrument_id, 0)
            projected = current + request.quantity * leg.ratio * leg.side.sign
            if current == 0 or current * projected < 0 or abs(projected) >= abs(current):
                raise ValueError("reduce-only combo does not strictly reduce every leg")
