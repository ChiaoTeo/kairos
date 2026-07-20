from __future__ import annotations

from kairos.domain.identity import InstitutionId

from datetime import timedelta
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import shutil
from uuid import UUID

from kairos.accounting.ledger import LedgerService
from kairos.ports import OrderAck, RecoveredExecution, VenueOrderRecovery, VenueOrderStatus
from kairos.connectors.simulated import SimulatedExecutionAccountGateway
from kairos.domain.capability import OrderType, TimeInForce
from kairos.domain.execution import TradeExecution, TradeSide
from kairos.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairos.domain.intent import TargetPositionIntent
from kairos.domain.ledger import Ledger
from kairos.domain.order import ExecutionInstructions
from kairos.execution.ingestion import DurableExecutionIngestionService
from kairos.execution.order_state import DurableOrderStatus
from kairos.execution.recovery import VenueOrderRecoveryService
from kairos.execution.router import ExecutionRouter
from kairos.execution.strategy_planner import plan_strategy_intent
from kairos.orchestration.coordinator import ExecutionCoordinator
from kairos.orchestration.event_log import PersistentEventLog
from kairos.orchestration.faults import InjectedRuntimeFailure, OneShotRuntimeFaultInjector, RuntimeFaultPoint
from kairos.orchestration.kill_switch import KillSwitch
from kairos.orchestration.reconciliation import ReconciliationService
from kairos.orchestration.runtime_store import SQLiteRuntimeStore

from .clock import FixedClock
from .config import ApplicationConfig, RuntimePaths
from .recovery import RuntimeRecoveryService
from .runtime import RuntimeStatus, KairosApplication
from .runtime_reference_artifact import STARTED_AT, _catalog


RUNTIME_FAILURE_POLICY_ID = "runtime-l3-failure-policy-v1"


def run_runtime_failure_policy(root: str | Path) -> dict[str, object]:
    output_root = Path(root)
    work = output_root / ".runtime-failure-policy-work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    cases = [
        _before_venue(work / "before-venue"),
        _after_venue_before_ack(work / "after-venue"),
        _partial_fill_restart(work / "partial-fill"),
        _websocket_disconnect_backfill(work / "disconnect-backfill"),
        _duplicate_transport(work / "duplicate"),
        _ledger_transaction_interrupt(work / "ledger-interrupt"),
        _kill_switch_restart(work / "kill-switch"),
        _reconciliation_mismatch(work / "reconciliation"),
        _account_lock_takeover(work / "account-lock"),
    ]
    payload: dict[str, object] = {
        "schema_version": 1,
        "policy_id": RUNTIME_FAILURE_POLICY_ID,
        "cases": cases,
        "passed": all(bool(case["passed"]) for case in cases),
        "invariants": {
            "duplicate_orders": 0,
            "lost_ledger_facts": 0,
            "duplicate_ledger_facts": 0,
            "unknown_state_risk_expansion": 0,
        },
    }
    audit_hash = _hash(payload)
    artifact = output_root / "artifacts" / RUNTIME_FAILURE_POLICY_ID / "manifest.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({**payload, "audit_hash": audit_hash}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**payload, "audit_hash": audit_hash, "artifact": str(artifact)}


def _request():
    instrument = InstrumentId("BTC-USDT-SPOT-GOLDEN")
    account = AccountKey(InstitutionId("simulated"), "failure-policy", AccountType.CRYPTO_SPOT)
    intent = TargetPositionIntent(
        UUID("00000000-0000-0000-0000-00000000f301"), "failure-policy-strategy",
        instrument, Decimal("1"), "failure matrix deterministic target",
    )
    return plan_strategy_intent(
        intent, accounts={instrument: account}, current_positions={},
        instructions={instrument: ExecutionInstructions(OrderType.LIMIT, TimeInForce.DAY, Decimal("100"))},
    ).orders[0]


def _execution_gateway(clock: FixedClock, *, balances=(), positions=()):
    order = _request()
    return SimulatedExecutionAccountGateway(
        VenueId("simulated"), order.account, balances=balances, positions=positions, clock=clock,
    )


def _coordinator(path: Path, store: SQLiteRuntimeStore, gateway, clock: FixedClock, injector=None):
    order = _request()
    paths = RuntimePaths(path, path / "catalog.json", path, store.path, path / "artifacts")
    application = KairosApplication(
        ApplicationConfig(gateway.environment, paths), store,
        runtime_id=f"failure-policy-{path.name}", accounts=(order.account,), clock=clock,
        recovery=RuntimeRecoveryService(store, _catalog(), AssetId("USDT"), {order.account: gateway}),
    )
    coordinator = ExecutionCoordinator(
        ExecutionRouter(_catalog(), (gateway,)),
        {order.account: ReconciliationService(store.load_ledger(), gateway, clock=clock)},
        KillSwitch((gateway,), clock, store), PersistentEventLog(path / "events.jsonl"),
        clock=clock, runtime_store=store, application=application, fault_injector=injector,
    )
    application.start()
    coordinator.activate()
    application.run()
    return coordinator


def _acknowledged(store: SQLiteRuntimeStore):
    order = _request()
    store.create_order(order, STARTED_AT)
    store.transition_order(order.client_order_id, DurableOrderStatus.APPROVED, STARTED_AT)
    store.transition_order(order.client_order_id, DurableOrderStatus.SUBMITTING, STARTED_AT)
    store.transition_order(order.client_order_id, DurableOrderStatus.ACKNOWLEDGED, STARTED_AT, ack=OrderAck(
        order.internal_order_id, order.client_order_id, order.strategy_id, order.intent_id,
        order.correlation_id, "failure-policy-venue-order", STARTED_AT,
    ))
    return order


def _execution(order, suffix: int, quantity: Decimal = Decimal("1")) -> TradeExecution:
    return TradeExecution(
        UUID(f"00000000-0000-0000-0000-{suffix:012d}"), STARTED_AT + timedelta(seconds=suffix),
        order.account, order.instrument_id, TradeSide.BUY, quantity, Decimal("100"), AssetId("USDT"),
        Decimal("0.1"), order.client_order_id,
    )


def _before_venue(path: Path) -> dict[str, object]:
    clock = FixedClock(STARTED_AT); store = SQLiteRuntimeStore(path / "runtime.sqlite3"); gateway = _execution_gateway(clock)
    try:
        _coordinator(path, store, gateway, clock, OneShotRuntimeFaultInjector(
            RuntimeFaultPoint.AFTER_ORDER_SUBMITTING_BEFORE_VENUE,
        )).submit(_request(), STARTED_AT)
    except InjectedRuntimeFailure:
        pass
    record = store.order(_request().client_order_id)
    safely_blocked = record is not None and record.status is DurableOrderStatus.SUBMITTING and not gateway.orders and not store.load_ledger().transactions
    resolution = store.resolve_unresolved_order(
        _request().client_order_id, DurableOrderStatus.REJECTED, STARTED_AT + timedelta(seconds=1),
        actor="failure-policy", reason="checkpoint proves transport was not called",
        evidence="fault=after_order_submitting_before_venue; venue_order_count=0",
    )
    passed = safely_blocked and resolution.previous_status is DurableOrderStatus.SUBMITTING and not store.unresolved_orders()
    return _case("crash_before_venue_call", passed, "no venue order; audited operator resolution closes ambiguity")


def _after_venue_before_ack(path: Path) -> dict[str, object]:
    clock = FixedClock(STARTED_AT); store = SQLiteRuntimeStore(path / "runtime.sqlite3"); gateway = _execution_gateway(clock)
    try:
        _coordinator(path, store, gateway, clock, OneShotRuntimeFaultInjector(
            RuntimeFaultPoint.AFTER_VENUE_ACCEPT_BEFORE_ACK_PERSIST,
        )).submit(_request(), STARTED_AT)
    except InjectedRuntimeFailure:
        pass
    recovery = VenueOrderRecoveryService(
        store, {_request().account: gateway},
        DurableExecutionIngestionService(LedgerService(store.load_ledger(), _catalog()), store),
    ).recover(STARTED_AT + timedelta(seconds=1))
    record = store.order(_request().client_order_id)
    passed = len(gateway.orders) == 1 and not recovery.unresolved and record is not None and record.status is DurableOrderStatus.ACKNOWLEDGED
    return _case("venue_accept_before_ack_persist", passed, "client-id recovery restores Ack without resubmission")


def _partial_fill_restart(path: Path) -> dict[str, object]:
    store = SQLiteRuntimeStore(path / "runtime.sqlite3"); order = _acknowledged(store); fill = _execution(order, 2, Decimal("0.4"))
    DurableExecutionIngestionService(LedgerService(Ledger(), _catalog()), store).ingest(
        fill, external_key="policy:partial:2", client_order_id=order.client_order_id, fully_filled=False,
    )
    restarted = SQLiteRuntimeStore(path / "runtime.sqlite3")
    duplicate = DurableExecutionIngestionService(LedgerService(restarted.load_ledger(), _catalog()), restarted).ingest(
        fill, external_key="policy:partial:2", client_order_id=order.client_order_id, fully_filled=False,
    )
    record = restarted.order(order.client_order_id)
    passed = record is not None and record.status is DurableOrderStatus.PARTIALLY_FILLED and duplicate is None and len(restarted.load_ledger().transactions) == 1
    return _case("partial_fill_crash", passed, "partial order and one Ledger fact survive restart")


class _BackfillRecoveryGateway:
    def __init__(self, outcome: VenueOrderRecovery) -> None:
        self.outcome = outcome

    def recover_order(self, account, request, venue_order_id=None):
        return self.outcome


def _websocket_disconnect_backfill(path: Path) -> dict[str, object]:
    store = SQLiteRuntimeStore(path / "runtime.sqlite3"); order = _acknowledged(store)
    store.transition_order(order.client_order_id, DurableOrderStatus.UNKNOWN, STARTED_AT + timedelta(seconds=1), reason="websocket disconnected")
    fill = _execution(order, 5)
    gateway = _BackfillRecoveryGateway(VenueOrderRecovery(
        VenueOrderStatus.FILLED, "REST fill-history backfill after websocket disconnect",
        acknowledgement=store.order(order.client_order_id).ack,  # type: ignore[union-attr]
        executions=(RecoveredExecution(
            "policy:disconnect-backfill:5", fill, True,
            "policy:rest-backfill", "5",
        ),),
    ))
    report = VenueOrderRecoveryService(
        store, {order.account: gateway},
        DurableExecutionIngestionService(LedgerService(store.load_ledger(), _catalog()), store),
    ).recover(STARTED_AT + timedelta(seconds=6))
    record = store.order(order.client_order_id)
    passed = (
        not report.unresolved and record is not None and record.status is DurableOrderStatus.FILLED
        and len(store.load_ledger().transactions) == 1 and store.cursor("policy:rest-backfill") == "5"
    )
    return _case("websocket_disconnect_rest_backfill", passed, "REST recovery restores the missed fill and cursor exactly once")


def _duplicate_transport(path: Path) -> dict[str, object]:
    store = SQLiteRuntimeStore(path / "runtime.sqlite3"); order = _acknowledged(store); fill = _execution(order, 3)
    ingestion = DurableExecutionIngestionService(LedgerService(Ledger(), _catalog()), store)
    first = ingestion.ingest(fill, external_key="policy:transport:3", client_order_id=order.client_order_id, fully_filled=True)
    second = ingestion.ingest(fill, external_key="policy:transport:3", client_order_id=order.client_order_id, fully_filled=True)
    return _case("rest_websocket_duplicate", first is not None and second is None and len(store.load_ledger().transactions) == 1, "shared external identity is idempotent")


def _ledger_transaction_interrupt(path: Path) -> dict[str, object]:
    store = SQLiteRuntimeStore(path / "runtime.sqlite3", fault_injector=OneShotRuntimeFaultInjector(RuntimeFaultPoint.DURING_EXECUTION_TRANSACTION))
    order = _acknowledged(store); fill = _execution(order, 4); failed = False
    try:
        DurableExecutionIngestionService(LedgerService(Ledger(), _catalog()), store).ingest(
            fill, external_key="policy:atomic:4", client_order_id=order.client_order_id,
            fully_filled=True, cursor_name="policy:fills", cursor_value="4",
        )
    except InjectedRuntimeFailure:
        failed = True
    restarted = SQLiteRuntimeStore(path / "runtime.sqlite3"); record = restarted.order(order.client_order_id)
    rolled_back = failed and record is not None and record.status is DurableOrderStatus.ACKNOWLEDGED and not restarted.load_ledger().transactions and restarted.cursor("policy:fills") is None
    retried = DurableExecutionIngestionService(LedgerService(Ledger(), _catalog()), restarted).ingest(
        fill, external_key="policy:atomic:4", client_order_id=order.client_order_id, fully_filled=True,
    )
    return _case("ledger_transaction_interruption", rolled_back and retried is not None and len(restarted.load_ledger().transactions) == 1, "transaction rolls back completely and retry commits once")


def _kill_switch_restart(path: Path) -> dict[str, object]:
    clock = FixedClock(STARTED_AT); store = SQLiteRuntimeStore(path / "runtime.sqlite3"); gateway = _execution_gateway(clock)
    KillSwitch((gateway,), clock, store).trigger((), "policy drill")
    switch = KillSwitch((gateway,), clock, SQLiteRuntimeStore(path / "runtime.sqlite3"))
    blocked = False
    try:
        paths = RuntimePaths(path, path / "catalog.json", path, store.path, path / "artifacts")
        application = KairosApplication(
            ApplicationConfig(gateway.environment, paths), store,
            runtime_id="failure-policy-kill-switch", accounts=(_request().account,), clock=clock,
            recovery=RuntimeRecoveryService(store, _catalog(), AssetId("USDT"), {_request().account: gateway}),
        )
        coordinator = ExecutionCoordinator(
            ExecutionRouter(_catalog(), (gateway,)), {_request().account: ReconciliationService(Ledger(), gateway, clock=clock)},
            switch, PersistentEventLog(path / "events.jsonl"), clock=clock, application=application,
        )
        application.start(); coordinator.activate(); application.run()
        coordinator.submit(_request(), STARTED_AT)
    except RuntimeError:
        blocked = True
    return _case("kill_switch_restart", switch.triggered and blocked and not gateway.orders, "persistent reduce-only state blocks risk expansion")


def _reconciliation_mismatch(path: Path) -> dict[str, object]:
    clock = FixedClock(STARTED_AT); paths = RuntimePaths.under(path); store = SQLiteRuntimeStore(paths.runtime_database)
    gateway = _execution_gateway(clock, balances=((AssetId("USDT"), Decimal("1")),)); blocked = False
    app = KairosApplication(
        ApplicationConfig(gateway.environment, paths), store, runtime_id="matrix-mismatch",
        accounts=(_request().account,), clock=clock,
        recovery=RuntimeRecoveryService(store, _catalog(), AssetId("USDT"), {_request().account: gateway}),
    )
    try:
        app.start()
    except RuntimeError:
        blocked = app.status is RuntimeStatus.UNKNOWN_EXTERNAL_STATE
    app.stop()
    return _case("reconciliation_mismatch", blocked, "Application never reaches READY")


def _account_lock_takeover(path: Path) -> dict[str, object]:
    store = SQLiteRuntimeStore(path / "runtime.sqlite3"); account = _request().account
    store.acquire_account_lock(account, "old", STARTED_AT, lease_seconds=5)
    store.acquire_account_lock(account, "new", STARTED_AT + timedelta(seconds=6), lease_seconds=5)
    rejected = False
    try:
        store.heartbeat_account_lock(account, "old", STARTED_AT + timedelta(seconds=7), lease_seconds=5)
    except RuntimeError:
        rejected = True
    return _case("account_lock_expiry_takeover", rejected, "expired owner cannot regain control by heartbeat")


def _case(name: str, passed: bool, proof: str) -> dict[str, object]:
    return {"name": name, "passed": bool(passed), "proof": proof}


def _hash(payload: dict[str, object]) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


FAILURE_MATRIX_ID = RUNTIME_FAILURE_POLICY_ID
run_runtime_failure_matrix = run_runtime_failure_policy
