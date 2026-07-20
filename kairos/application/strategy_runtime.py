from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Mapping
from uuid import NAMESPACE_URL, uuid5

from kairos.application.strategy_run_loop import GovernedStrategyRunLoop, StrategyRunHooks, StrategyRunResult
from kairos.contracts import BarPayload, CanonicalEventEnvelope
from kairos.domain.capability import OrderType, TimeInForce
from kairos.domain.execution import TradeExecution, TradeSide
from kairos.domain.identity import AccountKey, AssetId, InstrumentId, VenueId
from kairos.domain.intent import TargetExposureIntent
from kairos.domain.order import ExecutionInstructions
from kairos.domain.product import CryptoSpotSpec, ProductType
from kairos.domain.strategy_contract import EconomicIntent
from kairos.execution.ingestion import DurableExecutionIngestionService
from kairos.execution.order_state import DurableOrderStatus
from kairos.features import FactorSnapshot, SmaFactorConfig, SmaFactorRuntime
from kairos.market_data.stream import ConsumerGap, EventSource
from kairos.orchestration.runtime_store import SQLiteRuntimeStore
from kairos.orchestration.reconciliation import ReconciliationService
from kairos.ports import OrderRequest
from kairos.accounting.ledger import LedgerService
from kairos.domain.ledger import Ledger
from kairos.reference import (
    AssetDefinition, AssetType, ListingDefinition, ListingId, ReferenceCatalog,
    TradingRules, VenueDefinition, VenueType,
)
from kairos.reference.factory import publish_instrument
from kairos.risk.portfolio_governance import PositionSizer
from kairos.storage.codec import to_primitive
from kairos.strategies import GovernedStrategyRuntime, SmaCrossStrategy, SmaCrossStrategyConfig, StrategyContext
from kairos.strategies.specs import sma_strategy_spec


@dataclass(frozen=True, slots=True)
class RuntimeStrategyBinding:
    strategy_id: str
    model_kind: str
    service_id: str
    output_path: Path

    def manifest(self) -> dict[str, str]:
        return {
            "strategy_id": self.strategy_id,
            "model_kind": self.model_kind,
            "service_id": self.service_id,
            "output_path": str(self.output_path),
        }


StrategyRuntimeRunner = Callable[[], Awaitable[None]]
StrategyRuntimeRunnerFactory = Callable[[object], StrategyRuntimeRunner]


@dataclass(frozen=True, slots=True)
class RuntimeStrategyModelContext:
    strategy_lock: Mapping[str, object]
    feed_runtime: object
    run_directory: Path
    mode: str
    service_mode: str
    strategy_id: str
    output_path: Path
    parameters: Mapping[str, object]
    intent_bridge: "PaperIntentExecutionBridge | None"


@dataclass(frozen=True, slots=True)
class RuntimeStrategyModelSpec:
    kind: str
    aliases: tuple[str, ...]
    build: Callable[[RuntimeStrategyModelContext], StrategyRuntimeRunnerFactory]

    @property
    def accepted_kinds(self) -> tuple[str, ...]:
        return (self.kind, *self.aliases)


class RuntimeStrategyModelRegistry:
    def __init__(self, specs: tuple[RuntimeStrategyModelSpec, ...] = ()) -> None:
        self._specs: dict[str, RuntimeStrategyModelSpec] = {}
        for spec in specs:
            self.register(spec)

    def register(self, spec: RuntimeStrategyModelSpec) -> None:
        for kind in spec.accepted_kinds:
            if kind in self._specs:
                raise ValueError(f"runtime strategy model already registered: {kind}")
            self._specs[kind] = spec

    def resolve(self, kind: str) -> RuntimeStrategyModelSpec:
        try:
            return self._specs[kind]
        except KeyError as error:
            raise ValueError(
                f"Strategy Lock model {kind!r} has no built-in runtime runner; "
                f"registered={', '.join(self.kinds()) or '-'}"
            ) from error

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))


def builtin_runtime_strategy_model_registry() -> RuntimeStrategyModelRegistry:
    return RuntimeStrategyModelRegistry((
        RuntimeStrategyModelSpec(
            "sma-cross-v1",
            ("builtin.sma-cross-v1",),
            _sma_cross_runner_factory,
        ),
    ))


def strategy_runtime_runner_from_lock(
    strategy_lock: Mapping[str, object],
    feed_runtime: object | None,
    run_directory: str | Path,
    mode: str,
    intent_bridge: "PaperIntentExecutionBridge | None" = None,
    registry: RuntimeStrategyModelRegistry | None = None,
) -> tuple[object, dict[str, RuntimeStrategyBinding]]:
    """Build runners for Strategy Locks that declare a supported runtime model.

    The factory reads only the frozen Strategy Lock declaration and feed channel
    bindings. It does not re-hash editable local model files at run start.
    """

    model = strategy_lock.get("model")
    if not isinstance(model, Mapping):
        raise ValueError("Strategy Lock model must be an object")
    kind = str(model.get("kind") or model.get("strategy_id") or "")
    model_spec = (registry or builtin_runtime_strategy_model_registry()).resolve(kind)
    channels = getattr(feed_runtime, "channels", None)
    if not isinstance(channels, Mapping) or not channels:
        raise ValueError(f"Strategy runtime model {model_spec.kind!r} requires at least one feed runtime channel")

    strategy_id = str(strategy_lock.get("strategy_id") or "")
    output_path = Path(run_directory) / "strategy" / strategy_id / "runtime-result.json"
    parameters = model.get("parameters") if isinstance(model.get("parameters"), Mapping) else {}
    service_mode = "paper-trading" if mode == "paper" else mode
    binding = RuntimeStrategyBinding(strategy_id, model_spec.kind, f"strategy:{service_mode}:{strategy_id}", output_path)
    context = RuntimeStrategyModelContext(
        strategy_lock, feed_runtime, Path(run_directory), mode, service_mode, strategy_id,
        output_path, parameters, intent_bridge,
    )
    runner_factory = model_spec.build(context)

    return runner_factory, {strategy_id: binding}


def _sma_cross_runner_factory(context: RuntimeStrategyModelContext) -> StrategyRuntimeRunnerFactory:
    channels = getattr(context.feed_runtime, "channels")
    channel_id, source = next(iter(channels.items()))

    def runner_factory(service):
        async def run() -> None:
            result = await _run_sma_cross_strategy(
                source, context.strategy_lock, context.parameters, str(channel_id), context.intent_bridge,
            )
            if context.intent_bridge is not None:
                context.intent_bridge.close()
            _write_json(
                context.output_path,
                _strategy_result_payload(context.strategy_lock, service.service_id, str(channel_id), result),
            )
            await asyncio.Event().wait()

        return run

    return runner_factory


async def _run_sma_cross_strategy(
    source: object,
    strategy_lock: Mapping[str, object],
    parameters: Mapping[str, object],
    channel_id: str,
    intent_bridge: "PaperIntentExecutionBridge | None" = None,
) -> StrategyRunResult:
    fast = _positive_int(parameters.get("fast_window"), default=20, name="fast_window")
    slow = _positive_int(parameters.get("slow_window"), default=50, name="slow_window")
    if slow <= fast:
        raise ValueError("SMA runtime model requires slow_window > fast_window")
    instrument = str(parameters.get("instrument_id") or "")
    if not instrument:
        instrument = _instrument_from_lock(strategy_lock)
    approved_capital = Decimal(str(parameters.get("approved_capital") or "100000"))
    strategy_config = SmaCrossConfigShim(fast, slow)
    strategy_spec, policy = sma_strategy_spec(strategy_config)
    return await GovernedStrategyRunLoop(
        _CanonicalOnlySource(source),
        SmaFactorRuntime(SmaFactorConfig(fast, slow), input_identity=f"runtime-channel:{channel_id}"),
        GovernedStrategyRuntime(
            SmaCrossStrategy(SmaCrossStrategyConfig(InstrumentId(instrument))),
            strategy_spec,
            execution_policy_id=str(parameters.get("execution_policy_id") or policy.policy_id),
        ),
        lambda market: StrategyContext(market, object(), (), object()),
        approved_capital=approved_capital,
        hooks=_IntentBridgeHooks(intent_bridge) if intent_bridge is not None else None,
    ).run()


@dataclass(frozen=True, slots=True)
class SmaCrossConfigShim:
    fast_window: int
    slow_window: int


class _CanonicalOnlySource(EventSource[CanonicalEventEnvelope]):
    def __init__(self, source: object) -> None:
        if not hasattr(source, "events"):
            raise ValueError("strategy runtime source must expose events()")
        self.source = source

    async def events(self) -> AsyncIterator[CanonicalEventEnvelope]:
        async for event in self.source.events():
            if isinstance(event, ConsumerGap):
                continue
            yield event


@dataclass(frozen=True, slots=True)
class PaperIntentExecutionSubmission:
    economic_intent_id: str
    intent_id: str
    request: OrderRequest
    reference_price: Decimal
    target_quantity: Decimal


class PaperIntentExecutionBridge:
    """Convert strategy target intents into paper gateway order requests."""

    def __init__(
        self,
        *,
        account: AccountKey,
        output_path: str | Path,
        approved_capital: Decimal,
        cash_asset: AssetId = AssetId("USDT"),
        fee_bps: Decimal = Decimal("0"),
        lot_size: Decimal = Decimal("0.0001"),
        runtime_store: SQLiteRuntimeStore | None = None,
    ) -> None:
        if approved_capital <= 0 or lot_size <= 0 or fee_bps < 0:
            raise ValueError("paper intent bridge requires positive approved capital, lot size and non-negative fee")
        self.account = account
        self.output_path = Path(output_path)
        self.approved_capital = approved_capital
        self.cash_asset = cash_asset
        self.fee_bps = fee_bps
        self.lot_size = lot_size
        self.runtime_store = runtime_store
        self.sizer = PositionSizer()
        self._queue: asyncio.Queue[PaperIntentExecutionSubmission | None] = asyncio.Queue()
        self._virtual_positions: dict[InstrumentId, Decimal] = {}
        self._sequence = 0
        self._submitted: list[dict[str, object]] = []
        self._readiness: dict[str, object] | None = None
        self._closed = False

    def publish(self, event: CanonicalEventEnvelope, economic_intent: EconomicIntent) -> None:
        if self._closed:
            return
        if not isinstance(event.payload, BarPayload):
            return
        reference_price = event.payload.close
        for intent in economic_intent.intents:
            if not isinstance(intent, TargetExposureIntent):
                self._record({"status": "unsupported", "intent": to_primitive(intent)})
                continue
            sized = self.sizer.size(
                intent,
                approved_capital=min(self.approved_capital, economic_intent.risk_budget),
                reference_price=reference_price,
                lot_size=self.lot_size,
            )
            if not sized.approved or sized.intent is None:
                self._record({
                    "status": "rejected",
                    "intent_id": str(intent.intent_id),
                    "reason": sized.reason,
                })
                continue
            current = self._virtual_positions.get(intent.instrument_id, Decimal("0"))
            target = sized.intent.target_quantity
            delta = target - current
            if delta == 0:
                self._record({
                    "status": "already_at_target",
                    "intent_id": str(intent.intent_id),
                    "target_quantity": str(target),
                })
                continue
            self._sequence += 1
            side = TradeSide.BUY if delta > 0 else TradeSide.SELL
            quantity = abs(delta)
            request = OrderRequest(
                f"paper-{economic_intent.strategy_id}-{self._sequence}",
                f"paper-{economic_intent.strategy_id}-{intent.intent_id}-{self._sequence}",
                economic_intent.strategy_id,
                str(intent.intent_id),
                str(economic_intent.decision_id),
                self.account,
                intent.instrument_id,
                side,
                quantity,
                ExecutionInstructions(OrderType.MARKET, TimeInForce.IOC),
            )
            self._virtual_positions[intent.instrument_id] = target
            self._queue.put_nowait(PaperIntentExecutionSubmission(
                str(economic_intent.decision_id), str(intent.intent_id), request,
                reference_price, target,
            ))

    async def run_gateway(self, gateway: object) -> None:
        while True:
            submission = await self._queue.get()
            if submission is None:
                self._readiness = self._runtime_readiness(gateway)
                self._write_evidence()
                return
            ack = gateway.place_order(submission.request)
            fill = self._paper_fill(submission, ack)
            durable = self._commit_durable(submission.request, ack, fill)
            self._apply_projected_fill(gateway, fill, ack.venue_order_id)
            self._record({
                "status": "filled",
                "economic_intent_id": submission.economic_intent_id,
                "intent_id": submission.intent_id,
                "reference_price": str(submission.reference_price),
                "target_quantity": str(submission.target_quantity),
                "request": to_primitive(submission.request),
                "ack": to_primitive(ack),
                "fill": to_primitive(fill),
                "durable": durable,
                "proof": "paper simulated fill projection from strategy reference price",
            })

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)

    def manifest(self) -> dict[str, object]:
        return {
            "account": str(self.account),
            "output_path": str(self.output_path),
            "submitted_orders": sum(1 for item in self._submitted if item.get("status") in {"acknowledged", "filled"}),
            "filled_orders": sum(1 for item in self._submitted if item.get("status") == "filled"),
            "durable_executions": sum(
                1 for item in self._submitted
                if isinstance(item.get("durable"), dict) and item["durable"].get("committed")
            ),
            **({"readiness": self._readiness} if self._readiness is not None else {}),
        }

    def _paper_fill(self, submission: PaperIntentExecutionSubmission, ack) -> TradeExecution:
        request = submission.request
        notional = request.quantity * submission.reference_price
        fee = notional * self.fee_bps / Decimal("10000")
        return TradeExecution(
            uuid5(NAMESPACE_URL, f"paper-fill:{ack.venue_order_id}:{submission.reference_price}:{request.quantity}"),
            ack.accepted_at,
            request.account,
            request.instrument_id,
            request.side,
            request.quantity,
            submission.reference_price,
            self.cash_asset,
            fee,
            request.client_order_id,
        )

    def _apply_projected_fill(self, gateway: object, fill: TradeExecution, venue_order_id: str) -> None:
        positions = getattr(gateway, "positions", None)
        balances = getattr(gateway, "balances", None)
        orders = getattr(gateway, "orders", None)
        if isinstance(positions, dict):
            positions[fill.instrument_id] = positions.get(fill.instrument_id, Decimal("0")) + fill.quantity * fill.side.sign
        if isinstance(balances, dict):
            cash = balances.get(fill.fee_asset, Decimal("0"))
            balances[fill.fee_asset] = cash - fill.quantity * fill.price * fill.side.sign - fill.fee
        if isinstance(orders, dict):
            orders.pop(venue_order_id, None)

    def _commit_durable(self, request: OrderRequest, ack, fill: TradeExecution) -> dict[str, object]:
        if self.runtime_store is None:
            return {"enabled": False}
        store = self.runtime_store
        created = store.create_order(request, ack.accepted_at)
        if created.status is DurableOrderStatus.PLANNED:
            store.transition_order(request.client_order_id, DurableOrderStatus.APPROVED, ack.accepted_at)
            store.transition_order(request.client_order_id, DurableOrderStatus.SUBMITTING, ack.accepted_at)
        acknowledged = store.transition_order(
            request.client_order_id, DurableOrderStatus.ACKNOWLEDGED, ack.accepted_at, ack=ack,
        )
        catalog = _paper_reference_catalog(request.instrument_id, self.cash_asset, ack.accepted_at)
        ledger = store.load_ledger()
        transaction = DurableExecutionIngestionService(
            LedgerService(ledger, catalog), store,
        ).ingest(
            fill,
            external_key=f"paper:fill:{fill.execution_id}",
            client_order_id=request.client_order_id,
            fully_filled=True,
            cursor_name=f"paper:fills:{request.account.value}",
            cursor_value=f"{fill.timestamp.isoformat()}:{fill.execution_id}",
        )
        final = store.order(request.client_order_id)
        return {
            "enabled": True,
            "committed": transaction is not None,
            "order_status": final.status.value if final is not None else acknowledged.status.value,
            "runtime_database": str(store.path),
            "ledger_transactions": len(store.load_ledger().transactions),
        }

    def _runtime_readiness(self, gateway: object) -> dict[str, object]:
        if self.runtime_store is None:
            return {
                "kind": "paper_runtime_readiness",
                "ready": False,
                "reason": "runtime store is not bound",
            }
        report = ReconciliationService(
            self.runtime_store.load_ledger(),
            gateway,
            runtime_store=self.runtime_store,
        ).reconcile(self.account)
        filled = sum(1 for item in self._submitted if item.get("status") == "filled")
        durable = sum(
            1 for item in self._submitted
            if isinstance(item.get("durable"), dict) and item["durable"].get("committed")
        )
        ready = report.matched and filled == durable
        reason = (
            "paper runtime durable order/fill/ledger reconciliation matched"
            if ready else
            f"paper runtime readiness failed: reconciliation_differences={len(report.differences)};"
            f"filled_orders={filled};durable_executions={durable}"
        )
        return {
            "kind": "paper_runtime_readiness",
            "ready": ready,
            "reason": reason,
            "filled_orders": filled,
            "durable_executions": durable,
            "reconciliation": to_primitive(report),
        }

    def _record(self, row: dict[str, object]) -> None:
        self._submitted.append(row)
        self._write_evidence()

    def _write_evidence(self) -> None:
        _write_json(self.output_path, {
            "product": "run",
            "kind": "paper-intent-execution-bridge",
            "submissions": self._submitted,
            **({"readiness": self._readiness} if self._readiness is not None else {}),
        })


class _IntentBridgeHooks(StrategyRunHooks):
    def __init__(self, bridge: PaperIntentExecutionBridge) -> None:
        self.bridge = bridge

    def before_decision(
        self, event: CanonicalEventEnvelope, market: object, factor: FactorSnapshot,
    ) -> None:
        return None

    def on_intent(
        self, event: CanonicalEventEnvelope, market: object, factor: FactorSnapshot,
        intent: EconomicIntent,
    ) -> None:
        self.bridge.publish(event, intent)

    def on_end(self, context: StrategyContext) -> None:
        self.bridge.close()


def _instrument_from_lock(strategy_lock: Mapping[str, object]) -> str:
    data = strategy_lock.get("data")
    if isinstance(data, Mapping):
        for item in data.values():
            if isinstance(item, Mapping):
                value = item.get("instrument_id") or item.get("instrument")
                if value:
                    return str(value)
    raise ValueError("SMA runtime model requires model.parameters.instrument_id")


def _paper_reference_catalog(instrument_id: InstrumentId, cash_asset: AssetId, at: datetime) -> ReferenceCatalog:
    effective = at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
    catalog = ReferenceCatalog()
    base_asset = _paper_base_asset(instrument_id, cash_asset)
    assets = (
        AssetDefinition(base_asset, AssetType.CRYPTO, base_asset.value, effective, decimals=8),
        AssetDefinition(cash_asset, AssetType.CRYPTO, cash_asset.value, effective, decimals=8),
    )
    venue = VenueId("simulated")
    listing = ListingDefinition(
        ListingId(f"listing:simulated:{instrument_id.value}"),
        instrument_id,
        venue,
        instrument_id.value.rsplit(":", 1)[-1],
        cash_asset,
        TradingRules(Decimal("0.01"), Decimal("0.0001"), Decimal("0.0001")),
        effective,
        None,
        instrument_id.value.rsplit(":", 1)[-1],
    )
    publish_instrument(
        catalog,
        instrument_id=instrument_id,
        instrument_type=ProductType.CRYPTO_SPOT,
        display_name=instrument_id.value,
        contract_spec=CryptoSpotSpec(base_asset, cash_asset),
        trading_currency=cash_asset,
        listings=(listing,),
        effective_from=effective,
        asset_definitions=assets,
        venue_definitions=(VenueDefinition(venue, VenueType.CRYPTO_EXCHANGE, "Simulated", "UTC", effective),),
    )
    return catalog


def _paper_base_asset(instrument_id: InstrumentId, cash_asset: AssetId) -> AssetId:
    symbol = instrument_id.value.rsplit(":", 1)[-1]
    suffix = cash_asset.value
    if symbol.endswith(suffix) and len(symbol) > len(suffix):
        return AssetId(symbol[:-len(suffix)])
    return AssetId(f"BASE:{instrument_id.value}")


def _positive_int(value: object, *, default: int, name: str) -> int:
    try:
        result = int(value if value is not None else default)
    except (TypeError, ValueError) as error:
        raise ValueError(f"SMA runtime model parameter {name} must be an integer") from error
    if result < 1:
        raise ValueError(f"SMA runtime model parameter {name} must be positive")
    return result


def _strategy_result_payload(
    strategy_lock: Mapping[str, object],
    service_id: str,
    channel_id: str,
    result: StrategyRunResult,
) -> dict[str, object]:
    return {
        "product": "run",
        "kind": "strategy-runtime-result",
        "strategy_id": strategy_lock.get("strategy_id"),
        "strategy_lock_hash": strategy_lock.get("lock_hash"),
        "service_id": service_id,
        "source_channel": channel_id,
        "event_count": len(result.event_message_ids),
        "factor_snapshots": len(result.factor_snapshots),
        "decisions": len(result.decisions),
        "economic_intents": len(result.economic_intents),
        "factor_hash": result.factor_hash,
        "decision_hash": result.decision_hash,
        "intent_hash": result.intent_hash,
        "audit_hash": result.audit_hash,
        "result": to_primitive(result),
    }


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
