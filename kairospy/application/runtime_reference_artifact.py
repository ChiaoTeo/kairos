from __future__ import annotations

from kairospy.domain.identity import InstitutionId

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import shutil
from uuid import NAMESPACE_URL, UUID, uuid5

from kairospy.accounting.ledger import LedgerService
from kairospy.ports import Environment
from kairospy.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.domain.capability import OrderType, TimeInForce
from kairospy.domain.execution import TradeExecution, TradeSide
from kairospy.domain.identity import AccountKey, AccountType, AssetId, InstrumentId, VenueId
from kairospy.domain.intent import TargetPositionIntent
from kairospy.domain.ledger import Ledger
from kairospy.domain.market_data import Quote
from kairospy.domain.order import ExecutionInstructions
from kairospy.domain.product import CryptoSpotSpec, ProductType
from kairospy.reference import (
    AssetDefinition, AssetType, BrokerId, ExecutionRoute, ListingDefinition,
    ListingId, ReferenceCatalog, RouteId, TradingRules, VenueDefinition, VenueType,
)
from kairospy.reference.factory import publish_instrument
from kairospy.execution.ingestion import DurableExecutionIngestionService
from kairospy.execution.router import ExecutionRouter
from kairospy.execution.strategy_planner import plan_strategy_intent
from kairospy.orchestration.coordinator import ExecutionCoordinator
from kairospy.orchestration.event_log import PersistentEventLog
from kairospy.orchestration.kill_switch import KillSwitch
from kairospy.orchestration.reconciliation import ReconciliationService
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore
from kairospy.storage.codec import to_primitive

from .clock import FixedClock
from .config import ApplicationConfig, RuntimePaths
from .recovery import RuntimeRecoveryService
from .runtime import FunctionProbe, RuntimeStatus, KairosApplication


RUNTIME_REFERENCE_SCHEMA_VERSION = 1
RUNTIME_REFERENCE_SCENARIO_ID = "runtime-l2-spot-target-position-v1"
STARTED_AT = datetime(2026, 7, 17, 9, 30, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class RuntimeReferenceArtifactResult:
    scenario_id: str
    audit_hash: str
    artifact: Path
    payload: dict[str, object]


def run_runtime_reference_artifact(root: str | Path) -> RuntimeReferenceArtifactResult:
    """Run the formal L2 runtime chain and emit a deterministic restart audit.

    The scenario exercises Market Data -> Strategy -> Intent -> execution risk ->
    durable Order -> Venue Fill -> Ledger -> Portfolio/Risk -> restart reconciliation.
    It uses the same Application, Coordinator and Runtime Store as paper/live runtimes.
    """
    output_root = Path(root)
    work_root = output_root / ".runtime-reference-artifact-work"
    if work_root.exists():
        shutil.rmtree(work_root)
    paths = RuntimePaths.under(work_root)
    artifact_root = output_root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    store = SQLiteRuntimeStore(paths.runtime_database)
    catalog = _catalog()
    instrument_id = InstrumentId("BTC-USDT-SPOT-GOLDEN")
    account = AccountKey(InstitutionId("simulated"), "runtime-reference", AccountType.CRYPTO_SPOT)
    clock = FixedClock(STARTED_AT)
    venue = SimulatedExecutionAccountGateway(VenueId("simulated"), account, clock=clock)
    recovery = RuntimeRecoveryService(
        store, catalog, AssetId("USDT"), {account: venue}, marks={instrument_id: Decimal("100")},
    )
    application = KairosApplication(
        ApplicationConfig(Environment.TESTNET, paths), store,
        runtime_id="runtime-reference-before-restart", accounts=(account,), recovery=recovery, clock=clock,
        probes=(
            FunctionProbe("market_data", lambda: (True, "frozen quote is current")),
            FunctionProbe("execution", lambda: (venue.connected, "simulated venue connected")),
        ),
    )
    reconciliation = ReconciliationService(Ledger(), venue, clock=clock)
    coordinator = ExecutionCoordinator(
        ExecutionRouter(catalog, (venue,)), {account: reconciliation},
        KillSwitch((venue,), clock, store),
        PersistentEventLog(paths.root / "runtime" / "events.jsonl"),
        clock=clock, runtime_store=store, application=application,
    )

    stages: list[str] = []
    application.start()
    coordinator.activate()
    application.run()

    quote = Quote(instrument_id, Decimal("99"), Decimal("101"), Decimal("10"), Decimal("10"), clock.now())
    stages.append("market_data")
    intent = _strategy_decision(quote)
    stages.extend(("strategy", "intent"))
    plan = plan_strategy_intent(
        intent,
        accounts={instrument_id: account},
        current_positions={},
        instructions={instrument_id: ExecutionInstructions(
            OrderType.LIMIT, TimeInForce.DAY, Decimal("100"),
        )},
    )
    order = plan.orders[0]
    stages.append("risk")
    ack = coordinator.submit(order, clock.now())
    stages.append("order")

    clock.set(STARTED_AT + timedelta(seconds=1))
    execution = TradeExecution(
        UUID("00000000-0000-0000-0000-00000000a201"), clock.now(), account, instrument_id,
        TradeSide.BUY, Decimal("1"), Decimal("100"), AssetId("USDT"), Decimal("0.1"),
        order.client_order_id,
    )
    ingestion = DurableExecutionIngestionService(LedgerService(store.load_ledger(), catalog), store)
    transaction = ingestion.ingest(
        execution, external_key="simulated:runtime-reference:fill-1",
        client_order_id=order.client_order_id, fully_filled=True,
        cursor_name=f"simulated:fills:{account.value}", cursor_value="1",
    )
    assert transaction is not None
    venue.orders.pop(ack.venue_order_id)
    venue.positions[instrument_id] = Decimal("1")
    venue.balances[AssetId("USDT")] = Decimal("-100.1")
    stages.extend(("fill", "ledger"))

    application.stop()
    clock.set(STARTED_AT + timedelta(seconds=2))
    restarted_store = SQLiteRuntimeStore(paths.runtime_database)
    restarted_venue = SimulatedExecutionAccountGateway(
        VenueId("simulated"), account,
        balances=((AssetId("USDT"), Decimal("-100.1")),),
        positions=((instrument_id, Decimal("1")),), clock=clock,
    )
    restarted = KairosApplication(
        ApplicationConfig(Environment.TESTNET, paths), restarted_store,
        runtime_id="runtime-reference-after-restart", accounts=(account,), clock=clock,
        recovery=RuntimeRecoveryService(
            restarted_store, catalog, AssetId("USDT"), {account: restarted_venue},
            marks={instrument_id: Decimal("100")},
        ),
        probes=(FunctionProbe("market_data", lambda: (True, "frozen mark is current")),),
    )
    restarted.start()
    result = restarted.recovery_result
    assert result is not None and result.ready and restarted.status is RuntimeStatus.READY
    stages.extend(("portfolio", "reconciliation", "ready_after_restart"))

    durable_order = restarted_store.order(order.client_order_id)
    assert durable_order is not None
    ledger_payload = {
        "transactions": to_primitive(result.ledger.transactions),
        "entries": to_primitive(result.ledger.entries),
    }
    payload: dict[str, object] = {
        "schema_version": RUNTIME_REFERENCE_SCHEMA_VERSION,
        "scenario_id": RUNTIME_REFERENCE_SCENARIO_ID,
        "environment": Environment.TESTNET.value,
        "stages": stages,
        "market_data": to_primitive(quote),
        "intent": to_primitive(intent),
        "order": to_primitive(order),
        "acknowledgement": to_primitive(ack),
        "execution": to_primitive(execution),
        "durable_order_status": durable_order.status.value,
        "cursor": restarted_store.cursor(f"simulated:fills:{account.value}"),
        "ledger": ledger_payload,
        "ledger_hash": _hash(ledger_payload),
        "portfolio": to_primitive(result.portfolio),
        "risk": to_primitive(result.risk),
        "reconciliations": [
            {**to_primitive(report), "matched": report.matched} for report in result.reconciliations
        ],
        "restart_status": restarted.status.value,
    }
    audit_hash = _hash(payload)
    artifact = artifact_root / RUNTIME_REFERENCE_SCENARIO_ID / "manifest.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps({**payload, "audit_hash": audit_hash}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    restarted.stop()
    return RuntimeReferenceArtifactResult(RUNTIME_REFERENCE_SCENARIO_ID, audit_hash, artifact, payload)


def _strategy_decision(quote: Quote) -> TargetPositionIntent:
    if quote.bid is None or quote.ask is None or quote.bid <= 0 or quote.ask < quote.bid:
        raise ValueError("strategy requires a valid two-sided quote")
    return TargetPositionIntent(
        uuid5(NAMESPACE_URL, f"{RUNTIME_REFERENCE_SCENARIO_ID}:{quote.instrument_id.value}"),
        "runtime-reference-strategy-v1", quote.instrument_id, Decimal("1"),
        "deterministic target generated from governed quote",
    )


def _catalog() -> ReferenceCatalog:
    catalog = ReferenceCatalog(); instrument_id = InstrumentId("BTC-USDT-SPOT-GOLDEN")
    effective_from = datetime(2020, 1, 1, tzinfo=timezone.utc)
    publish_instrument(
        catalog, instrument_id=instrument_id, instrument_type=ProductType.CRYPTO_SPOT,
        display_name="BTC/USDT", contract_spec=CryptoSpotSpec(AssetId("BTC"), AssetId("USDT"), Decimal("10")),
        trading_currency=AssetId("USDT"), listings=(ListingDefinition(
            ListingId("listing:simulated:BTCUSDT"), instrument_id, VenueId("simulated"), "BTCUSDT", AssetId("USDT"),
            TradingRules(Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), minimum_notional=Decimal("10")),
            effective_from,
        ),), effective_from=effective_from,
        asset_definitions=(
            AssetDefinition(AssetId("BTC"), AssetType.CRYPTO, "Bitcoin", effective_from, decimals=8),
            AssetDefinition(AssetId("USDT"), AssetType.CRYPTO, "Tether USD", effective_from, decimals=6),
        ),
        venue_definitions=(VenueDefinition(VenueId("simulated"), VenueType.CRYPTO_EXCHANGE, "Simulated", "UTC", effective_from),),
    )
    listing_id = ListingId("listing:simulated:BTCUSDT")
    for account_id in ("runtime-reference", "failure-policy"):
        account = AccountKey(InstitutionId("simulated"), account_id, AccountType.CRYPTO_SPOT)
        catalog.routes.add(ExecutionRoute(
            RouteId(f"route:simulated:{account_id}"), BrokerId("simulated"), account, listing_id, effective_from,
        ))
    return catalog


def _hash(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return sha256(encoded.encode("utf-8")).hexdigest()
