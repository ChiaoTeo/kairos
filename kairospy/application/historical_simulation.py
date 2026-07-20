from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from kairospy.accounting.ledger import LedgerService
from kairospy.ports import Environment
from kairospy.connectors.simulated import SimulatedExecutionAccountGateway
from kairospy.contracts import BarPayload, CanonicalEventEnvelope
from kairospy.domain.capability import OrderType, TimeInForce
from kairospy.domain.execution import TradeExecution, TradeSide
from kairospy.domain.identity import AccountKey, AssetId, InstrumentId, VenueId
from kairospy.domain.intent import CancelIntent, TargetExposureIntent
from kairospy.domain.ledger import Ledger, LedgerBook
from kairospy.domain.order import ExecutionInstructions
from kairospy.execution.ingestion import DurableExecutionIngestionService
from kairospy.execution.order_state import DurableOrderStatus
from kairospy.execution.router import ExecutionRouter
from kairospy.execution.strategy_planner import plan_economic_intent
from kairospy.features import SmaFactorConfig, SmaFactorRuntime
from kairospy.orchestration.coordinator import ExecutionCoordinator
from kairospy.orchestration.event_log import PersistentEventLog
from kairospy.orchestration.kill_switch import KillSwitch
from kairospy.orchestration.reconciliation import ReconciliationService
from kairospy.orchestration.runtime_store import SQLiteRuntimeStore
from kairospy.reference import (
    AssetDefinition, AssetType, BrokerId, ExecutionRoute, ListingDefinition, ListingId,
    ReferenceCatalog, RouteId, TradingRules, VenueDefinition, VenueType,
)
from kairospy.reference.factory import publish_instrument
from kairospy.domain.product import CryptoSpotSpec, ProductType
from kairospy.risk.portfolio_governance import PositionSizer
from kairospy.strategies import GovernedStrategyRuntime, SmaCrossStrategy, SmaCrossStrategyConfig, StrategyContext
from kairospy.strategies.specs import sma_strategy_spec
from kairospy.strategies.sma_cross_study_backtest import SmaCrossConfig

from .clock import FixedClock
from .config import ApplicationConfig, RuntimePaths
from .recovery import RuntimeRecoveryService
from .runtime import FunctionProbe, RuntimeStatus, KairosApplication
from .strategy_run_loop import GovernedStrategyRunLoop, StrategyRunResult


@dataclass(frozen=True, slots=True)
class SimulationPortfolioState:
    cash: Decimal
    position: Decimal


@dataclass(frozen=True, slots=True)
class HistoricalSimulationResult:
    strategy_run: StrategyRunResult
    orders: int
    fills: int
    final_cash: Decimal
    final_position: Decimal
    restart_ready: bool
    runtime_database: Path
    fees:Decimal=Decimal("0")


class _SmaSimulationHooks:
    def __init__(self, *, clock: FixedClock, store: SQLiteRuntimeStore, catalog: ReferenceCatalog,
                 account: AccountKey, instrument_id: InstrumentId, cash_asset: AssetId,
                 approved_capital: Decimal, lot_size: Decimal, fee_bps: Decimal,
                 policy, coordinator: ExecutionCoordinator,
                 venue: SimulatedExecutionAccountGateway) -> None:
        self.clock = clock; self.store = store; self.catalog = catalog; self.account = account
        self.instrument_id = instrument_id; self.cash_asset = cash_asset
        self.approved_capital = approved_capital; self.lot_size = lot_size; self.fee_bps = fee_bps
        self.policy = policy; self.coordinator = coordinator; self.venue = venue
        self.sizer = PositionSizer(); self.pending = []
        self.orders = 0; self.fills = 0; self.last_price = Decimal("0")

    def before_decision(self, event, market, factor) -> None:
        self.clock.set(event.available_time)
        payload = event.payload
        if not isinstance(payload, BarPayload):
            return
        self.last_price = payload.close
        pending, self.pending = self.pending, []
        for request, ack in pending:
            self._fill(request, ack, payload.close, event)

    def on_intent(self, event, market, factor, economic_intent) -> None:
        if len(economic_intent.intents) != 1 or not isinstance(economic_intent.intents[0], TargetExposureIntent):
            raise TypeError("SMA historical simulation requires one TargetExposureIntent")
        equity = self.cash() + self.position() * self.last_price
        execution_buffer = (self.fee_bps + self.policy.maximum_slippage_bps) / Decimal("10000")
        sizing_capital = min(self.approved_capital, max(Decimal("0"), equity)) * (Decimal("1")-execution_buffer)
        sized = self.sizer.size(
            economic_intent.intents[0], approved_capital=sizing_capital,
            reference_price=self.last_price, lot_size=self.lot_size,
        )
        if not sized.approved or sized.intent is None:
            return
        current = self.position()
        executable = replace(economic_intent, intents=(sized.intent,))
        plan = plan_economic_intent(
            executable, policy=self.policy, accounts={self.instrument_id: self.account},
            current_positions={self.instrument_id: current},
            instructions={self.instrument_id: ExecutionInstructions(OrderType.MARKET, TimeInForce.IOC)},
            now=self.clock.now(),
        )
        for item in plan.plans:
            for request in item.orders:
                ack = self.coordinator.submit(request, self.clock.now())
                self.pending.append((request, ack)); self.orders += 1

    def on_end(self, context) -> None:
        # A last-bar decision remains a working order because no future market event exists to fill it.
        return None

    def position(self) -> Decimal:
        ledger = self.store.load_ledger()
        return sum((
            entry.amount for entry in ledger.entries
            if entry.account == self.account and entry.book is LedgerBook.POSITION
            and entry.instrument_id == self.instrument_id
        ), Decimal("0"))

    def cash(self) -> Decimal:
        return self.store.load_ledger().book_balance(self.account, LedgerBook.CASH, self.cash_asset)

    def _fill(self, request, ack, price: Decimal, event: CanonicalEventEnvelope) -> None:
        quantity = request.quantity
        fee_rate = self.fee_bps / Decimal("10000")
        if request.side is TradeSide.BUY:
            affordable = self.cash() / (price * (Decimal("1") + fee_rate))
            quantity = min(quantity, (affordable // self.lot_size) * self.lot_size)
        else:
            quantity = min(quantity, max(Decimal("0"), self.position()))
        if quantity <= 0:
            self.store.transition_order(
                request.client_order_id, DurableOrderStatus.CANCELLED, event.available_time,
                reason="IOC has no affordable/reducible quantity",
            )
            self.venue.orders.pop(ack.venue_order_id, None)
            return
        fee = quantity * price * fee_rate
        execution = TradeExecution(
            uuid5(NAMESPACE_URL, f"historical-simulation:{request.client_order_id}:{event.message_id}"),
            event.available_time, self.account, request.instrument_id, request.side,
            quantity, price, self.cash_asset, fee, request.client_order_id,
        )
        ingestion = DurableExecutionIngestionService(
            LedgerService(self.store.load_ledger(), self.catalog), self.store,
        )
        transaction = ingestion.ingest(
            execution, external_key=f"historical-simulation:{execution.execution_id}",
            client_order_id=request.client_order_id, fully_filled=quantity == request.quantity,
            cursor_name=f"historical-simulation:fills:{self.account.value}",
            cursor_value=f"{event.available_time.isoformat()}:{execution.execution_id}",
        )
        if transaction is None:
            return
        if quantity != request.quantity:
            self.store.transition_order(
                request.client_order_id, DurableOrderStatus.CANCELLED, event.available_time,
                reason="IOC remainder cancelled after affordable partial fill",
            )
        self.fills += 1
        self.venue.orders.pop(ack.venue_order_id, None)
        current = self.venue.positions.get(self.instrument_id, Decimal("0"))
        self.venue.positions[self.instrument_id] = current + quantity * request.side.sign
        cash = self.venue.balances.get(self.cash_asset, Decimal("0"))
        self.venue.balances[self.cash_asset] = cash - quantity * request.side.sign * price - fee


async def run_sma_historical_simulation(
    *, root: str | Path, events: tuple[CanonicalEventEnvelope, ...], catalog: ReferenceCatalog,
    instrument_id: InstrumentId, account: AccountKey, cash_asset: AssetId,
    initial_cash: Decimal = Decimal("100000"), factor_config: SmaFactorConfig = SmaFactorConfig(),
    lot_size: Decimal = Decimal("0.0001"), fee_bps: Decimal = Decimal("10"),
    input_identity: str = "historical-simulation",
    mode: str = "historical-simulation", environment: Environment = Environment.TESTNET,
) -> HistoricalSimulationResult:
    if not events:
        raise ValueError("historical simulation requires canonical events")
    paths = RuntimePaths.under(Path(root))
    store = SQLiteRuntimeStore(paths.runtime_database)
    if not store.load_ledger().transactions:
        seed = Ledger(); LedgerService(seed, catalog).deposit(
            account, cash_asset, initial_cash, events[0].available_time, "historical-simulation-capital",
        ); store.import_ledger(seed)
    clock = FixedClock(events[0].available_time)
    venue = SimulatedExecutionAccountGateway(
        VenueId("simulated"), account, balances=((cash_asset, initial_cash),), clock=clock,
        environment=environment,
    )
    recovery = RuntimeRecoveryService(
        store, catalog, cash_asset, {account: venue}, marks={instrument_id: _close(events[0])},
    )
    application = KairosApplication(
        ApplicationConfig(environment, paths), store,
        runtime_id=f"sma-{mode}", accounts=(account,), recovery=recovery, clock=clock,
        probes=(FunctionProbe("market_data", lambda: (True, "frozen canonical bars ready")),),
    )
    ledger = store.load_ledger()
    reconciliation = ReconciliationService(ledger, venue, runtime_store=store, clock=clock)
    coordinator = ExecutionCoordinator(
        ExecutionRouter(catalog, (venue,)), {account: reconciliation},
        KillSwitch((venue,), clock, store), PersistentEventLog(paths.root/"runtime"/"events.jsonl"),
        clock=clock, runtime_store=store, application=application,
    )
    application.start(); coordinator.activate(); application.run()
    spec, policy = sma_strategy_spec(SmaCrossConfig(
        factor_config.fast_window, factor_config.slow_window, initial_cash, fee_bps,
    ))
    hooks = _SmaSimulationHooks(
        clock=clock, store=store, catalog=catalog, account=account, instrument_id=instrument_id,
        cash_asset=cash_asset, approved_capital=initial_cash, lot_size=lot_size, fee_bps=fee_bps,
        policy=policy, coordinator=coordinator, venue=venue,
    )
    from kairospy.market_data import IterableEventSource
    result = await GovernedStrategyRunLoop(
        IterableEventSource(events), SmaFactorRuntime(factor_config, input_identity=input_identity),
        GovernedStrategyRuntime(
            SmaCrossStrategy(SmaCrossStrategyConfig(instrument_id)), spec,
            execution_policy_id=policy.policy_id,
        ),
        lambda market: StrategyContext(
            market, SimulationPortfolioState(hooks.cash(), hooks.position()), (), catalog,
        ),
        approved_capital=initial_cash, hooks=hooks,
    ).run()
    application.stop()

    clock.set(events[-1].available_time)
    restarted = KairosApplication(
        ApplicationConfig(environment, paths), store,
        runtime_id=f"sma-{mode}-restart", accounts=(account,), clock=clock,
        recovery=RuntimeRecoveryService(
            store, catalog, cash_asset, {account: venue}, marks={instrument_id: hooks.last_price},
        ),
        probes=(FunctionProbe("market_data", lambda: (True, "frozen mark ready")),),
    )
    restarted.start()
    restart_ready = restarted.status is RuntimeStatus.READY
    restarted.stop()
    return HistoricalSimulationResult(
        result, hooks.orders, hooks.fills, hooks.cash(), hooks.position(), restart_ready,
        paths.runtime_database,store.load_ledger().book_balance(account,LedgerBook.FEE_EXPENSE,cash_asset),
    )


def _close(event: CanonicalEventEnvelope) -> Decimal:
    if not isinstance(event.payload, BarPayload):
        raise TypeError("SMA historical simulation requires Bar events")
    return event.payload.close


def build_simulated_spot_catalog(
    *, instrument_id: InstrumentId, account: AccountKey, base_asset: AssetId,
    quote_asset: AssetId, effective_from: datetime,
) -> ReferenceCatalog:
    catalog = ReferenceCatalog()
    listing_id = ListingId(f"listing:simulated:{instrument_id.value}")
    publish_instrument(
        catalog, instrument_id=instrument_id, instrument_type=ProductType.CRYPTO_SPOT,
        display_name=f"{base_asset.value}/{quote_asset.value}",
        contract_spec=CryptoSpotSpec(base_asset, quote_asset, Decimal("10")),
        trading_currency=quote_asset,
        listings=(ListingDefinition(
            listing_id, instrument_id, VenueId("simulated"), instrument_id.value, quote_asset,
            TradingRules(Decimal("0.01"), Decimal("0.0001"), Decimal("0.0001"), minimum_notional=Decimal("1")),
            effective_from,
        ),), effective_from=effective_from,
        asset_definitions=(
            AssetDefinition(base_asset, AssetType.CRYPTO, base_asset.value, effective_from, decimals=8),
            AssetDefinition(quote_asset, AssetType.CRYPTO, quote_asset.value, effective_from, decimals=8),
        ),
        venue_definitions=(VenueDefinition(
            VenueId("simulated"), VenueType.CRYPTO_EXCHANGE, "Simulated", "UTC", effective_from,
        ),),
    )
    catalog.routes.add(ExecutionRoute(
        RouteId(f"route:simulated:{account.account_id}:{instrument_id.value}"),
        BrokerId("simulated"), account, listing_id, effective_from,
    ))
    return catalog
