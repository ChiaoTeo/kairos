from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from kairospy.execution.ports import ComboLegRequest, ComboOrderRequest, OrderRequest
from kairospy.execution.orders import OrderType
from kairospy.execution.events import TradeSide
from kairospy.identity import AccountRef, InstrumentId
from kairospy.strategy.archetypes import CashAndCarryIntent, CoveredCallIntent, ProtectivePutIntent
from kairospy.strategy.intents import (
    CancelIntent, CloseStructureIntent, HedgeIntent, OpenStructureIntent, TargetPositionIntent,
    TransferIntent,
)
from kairospy.execution.orders import ExecutionInstructions
from kairospy.strategy.contracts import EconomicIntent
from kairospy.execution.policy import ExecutionPolicy
from kairospy.execution.planner import LeggingPolicy


@dataclass(frozen=True, slots=True)
class StrategyExecutionPlan:
    intent_id: str
    orders: tuple[OrderRequest, ...] = ()
    combo_orders: tuple[ComboOrderRequest, ...] = ()
    transfers: tuple[TransferIntent, ...] = ()
    cancellations: tuple[CancelIntent, ...] = ()


@dataclass(frozen=True, slots=True)
class EconomicExecutionPlan:
    decision_id: str
    strategy_id: str
    strategy_spec_hash: str
    execution_policy_id: str
    plans: tuple[StrategyExecutionPlan, ...]


def plan_economic_intent(economic_intent: EconomicIntent, *, policy: ExecutionPolicy,
                         accounts: dict[InstrumentId, AccountRef], current_positions: dict[InstrumentId, Decimal],
                         instructions: dict[InstrumentId, ExecutionInstructions], now,
                         working_orders: tuple[OrderRequest, ...] = (), attempt: int = 1) -> EconomicExecutionPlan:
    if now.tzinfo is None: raise ValueError("planning time must be timezone-aware")
    if attempt < 1: raise ValueError("execution attempt must be positive")
    if now > economic_intent.valid_until: raise ValueError("economic intent has expired")
    if economic_intent.execution_policy_id != policy.policy_id:
        raise ValueError("economic intent execution policy does not match supplied policy")
    if economic_intent.atomicity_preference=="atomic" and policy.legging_policy is LeggingPolicy.SEQUENTIAL:
        raise ValueError("atomic economic intent cannot use sequential legging policy")
    plans=tuple(plan_strategy_intent(intent,accounts=accounts,current_positions=current_positions,
                instructions=instructions,working_orders=working_orders,attempt=attempt)
                for intent in economic_intent.intents)
    return EconomicExecutionPlan(str(economic_intent.decision_id),economic_intent.strategy_id,
        economic_intent.strategy_spec_hash,policy.policy_id,plans)


def plan_strategy_intent(intent, *, accounts: dict[InstrumentId, AccountRef], current_positions: dict[InstrumentId, Decimal], instructions: dict[InstrumentId, ExecutionInstructions], working_orders: tuple[OrderRequest, ...] = (), attempt: int = 1) -> StrategyExecutionPlan:
    if attempt < 1: raise ValueError("execution attempt must be positive")
    combo_orders = ()
    transfers = ()
    cancellations = ()
    if isinstance(intent, TargetPositionIntent):
        delta = intent.target_quantity - current_positions.get(intent.instrument_id, Decimal("0")) - _working_quantity(intent, intent.instrument_id, working_orders)
        orders = () if delta == 0 else (_order(intent, intent.instrument_id, accounts, instructions, TradeSide.BUY if delta > 0 else TradeSide.SELL, abs(delta), 1, attempt),)
    elif isinstance(intent, CoveredCallIntent):
        orders = (_order(intent, intent.option_id, accounts, instructions, TradeSide.SELL, intent.contracts, 1, attempt),)
    elif isinstance(intent, ProtectivePutIntent):
        orders = (_order(intent, intent.option_id, accounts, instructions, TradeSide.BUY, intent.contracts, 1, attempt),)
    elif isinstance(intent, CashAndCarryIntent):
        values = []
        for index, (instrument_id, delta) in enumerate((
            (intent.spot_instrument_id, intent.spot_quantity),
            (intent.derivative_instrument_id, intent.derivative_quantity),
        ), 1):
            if delta:
                values.append(_order(intent, instrument_id, accounts, instructions, TradeSide.BUY if delta > 0 else TradeSide.SELL, abs(delta), index, attempt))
        orders = tuple(values)
    elif isinstance(intent, HedgeIntent):
        delta = intent.target_delta - current_positions.get(intent.hedge_instrument_id, Decimal("0")) - _working_quantity(intent, intent.hedge_instrument_id, working_orders)
        orders = () if delta == 0 else (_order(
            intent, intent.hedge_instrument_id, accounts, instructions,
            TradeSide.BUY if delta > 0 else TradeSide.SELL, abs(delta), 1, attempt,
        ),)
    elif isinstance(intent, (OpenStructureIntent, CloseStructureIntent)):
        orders = ()
        combo_orders = (_combo_order(intent, accounts, attempt),)
    elif isinstance(intent, TransferIntent):
        orders, transfers = (), (intent,)
    elif isinstance(intent, CancelIntent):
        orders, cancellations = (), (intent,)
    else:
        raise TypeError(f"unsupported strategy intent: {type(intent).__name__}")
    return StrategyExecutionPlan(str(intent.intent_id), orders, combo_orders, transfers, cancellations)


def _order(intent, instrument_id, accounts, instructions, side, quantity, index, attempt):
    if instrument_id not in accounts or instrument_id not in instructions:
        raise LookupError(f"execution configuration missing for {instrument_id}")
    correlation = str(uuid5(NAMESPACE_URL, f"strategy-plan:{intent.strategy_id}:{intent.intent_id}"))
    suffix = f"{intent.intent_id}:{index}" if attempt == 1 else f"{intent.intent_id}:{attempt}:{index}"
    internal = str(uuid5(NAMESPACE_URL, f"strategy-order:{suffix}"))
    client = (f"{intent.strategy_id}-{intent.intent_id}-{index}" if attempt == 1 else
              f"{intent.strategy_id}-{intent.intent_id}-{attempt}-{index}")
    return OrderRequest(
        internal, client, intent.strategy_id,
        str(intent.intent_id), correlation, accounts[instrument_id], instrument_id, side, quantity,
        instructions[instrument_id],
    )


def _combo_order(intent, accounts: dict[InstrumentId, AccountRef], attempt: int) -> ComboOrderRequest:
    missing = [leg.instrument_id for leg in intent.legs if leg.instrument_id not in accounts]
    if missing:
        raise LookupError(f"execution account missing for combo legs: {', '.join(item.value for item in missing)}")
    leg_accounts = {accounts[leg.instrument_id] for leg in intent.legs}
    if len(leg_accounts) != 1:
        raise ValueError("native combo legs must use the same account")
    correlation = str(uuid5(NAMESPACE_URL, f"strategy-plan:{intent.strategy_id}:{intent.intent_id}"))
    suffix = str(intent.intent_id) if attempt == 1 else f"{intent.intent_id}:{attempt}"
    internal = str(uuid5(NAMESPACE_URL, f"strategy-combo:{suffix}"))
    client = (f"{intent.strategy_id}-{intent.intent_id}-combo" if attempt == 1 else
              f"{intent.strategy_id}-{intent.intent_id}-{attempt}-combo")
    order_type = OrderType.LIMIT if intent.limit_price is not None else OrderType.MARKET
    return ComboOrderRequest(
        internal, client, intent.strategy_id,
        str(intent.intent_id), correlation, next(iter(leg_accounts)),
        tuple(ComboLegRequest(leg.instrument_id, leg.side, leg.ratio) for leg in intent.legs),
        Decimal(intent.quantity),
        ExecutionInstructions(order_type, intent.time_in_force, intent.limit_price),
    )


def _working_quantity(intent, instrument_id: InstrumentId, working_orders: tuple[OrderRequest, ...]) -> Decimal:
    return sum((
        order.quantity * order.side.sign for order in working_orders
        if order.strategy_id == intent.strategy_id and order.instrument_id == instrument_id
    ), Decimal("0"))
