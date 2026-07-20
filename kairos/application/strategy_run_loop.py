from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
import json
from typing import Callable, Protocol

from kairos.backtest.feed import MarketSnapshot
from kairos.contracts import CanonicalEventEnvelope
from kairos.domain.strategy_contract import EconomicIntent
from kairos.features.runtime import FactorRuntime, FactorSnapshot
from kairos.execution.intent_status import IntentExecutionTracker
from kairos.market_data.projections import CanonicalBarSeriesProjection
from kairos.market_data.stream import EventSource
from kairos.storage.codec import to_primitive
from kairos.strategies.strategy_protocols import StrategyContext, StrategyDecision
from kairos.strategies.runtime import GovernedStrategyRuntime


@dataclass(frozen=True, slots=True)
class StrategyRunResult:
    event_message_ids: tuple[str, ...]
    factor_snapshots: tuple[FactorSnapshot, ...]
    decisions: tuple[StrategyDecision, ...]
    economic_intents: tuple[EconomicIntent, ...]
    factor_hash: str
    decision_hash: str
    intent_hash: str
    audit_hash: str


class StrategyRunHooks(Protocol):
    def before_decision(
        self, event: CanonicalEventEnvelope, market: MarketSnapshot, factor: FactorSnapshot,
    ) -> None: ...

    def on_intent(
        self, event: CanonicalEventEnvelope, market: MarketSnapshot, factor: FactorSnapshot,
        intent: EconomicIntent,
    ) -> None: ...

    def on_end(self, context: StrategyContext) -> None: ...


class CanonicalBarMarketProjection:
    def __init__(self) -> None:
        self._bars = CanonicalBarSeriesProjection()
        self._sequence = 0

    def apply(self, event: CanonicalEventEnvelope) -> MarketSnapshot | None:
        bar = self._bars.apply(event)
        if bar is None:
            return None
        self._sequence += 1
        return MarketSnapshot(
            bar.end, (), ((bar.instrument_id, bar.close),), sequence=self._sequence,
            available_instruments=(bar.instrument_id,),
        )


class GovernedStrategyRunLoop:
    """Shared deterministic decision loop used before any execution driver boundary."""

    def __init__(
        self,
        source: EventSource[CanonicalEventEnvelope],
        factor_runtime: FactorRuntime,
        strategy_runtime: GovernedStrategyRuntime,
        context_factory: Callable[[MarketSnapshot], StrategyContext],
        *,
        approved_capital: Decimal,
        hooks: StrategyRunHooks | None = None,
        intent_tracker: IntentExecutionTracker | None = None,
    ) -> None:
        if approved_capital <= 0:
            raise ValueError("strategy run requires positive approved capital")
        self.source = source
        self.factor_runtime = factor_runtime
        self.strategy_runtime = strategy_runtime
        self.context_factory = context_factory
        self.approved_capital = approved_capital
        self.hooks = hooks
        self.intent_tracker = intent_tracker or IntentExecutionTracker()

    async def run(self) -> StrategyRunResult:
        market_projection = CanonicalBarMarketProjection()
        event_ids: list[str] = []
        factors: list[FactorSnapshot] = []
        intents: list[EconomicIntent] = []
        last_context: StrategyContext | None = None
        started = False
        async for event in self.source.events():
            market = market_projection.apply(event)
            factor = self.factor_runtime.update(event)
            if market is None or factor is None:
                continue
            if self.hooks is not None:
                self.hooks.before_decision(event, market, factor)
            event_ids.append(str(event.message_id))
            factors.append(factor)
            base = self.context_factory(market)
            context = replace(
                base, approved_capital=self.approved_capital, factor_snapshots=(factor,),
                intent_executions=self.intent_tracker.views,
            )
            if not started:
                if intent := self.strategy_runtime.on_start(context):
                    intents.append(intent)
                    for item in intent.intents:
                        self.intent_tracker.publish(item)
                started = True
            if intent := self.strategy_runtime.on_market(context):
                intents.append(intent)
                for item in intent.intents:
                    self.intent_tracker.publish(item)
                if self.hooks is not None:
                    self.hooks.on_intent(event, market, factor, intent)
            last_context = context
        if last_context is not None:
            if intent := self.strategy_runtime.on_end(last_context):
                intents.append(intent)
                for item in intent.intents:
                    self.intent_tracker.publish(item)
            if self.hooks is not None:
                self.hooks.on_end(last_context)
        decisions = tuple(self.strategy_runtime.strategy.decisions)
        factor_hash = _hash(factors)
        decision_hash = _hash(decisions)
        intent_hash = _hash(intents)
        audit_hash = _hash({
            "events": event_ids,
            "factor_hash": factor_hash,
            "decision_hash": decision_hash,
            "intent_hash": intent_hash,
        })
        return StrategyRunResult(
            tuple(event_ids), tuple(factors), decisions, tuple(intents), factor_hash,
            decision_hash, intent_hash, audit_hash,
        )


def _hash(value: object) -> str:
    encoded = json.dumps(
        to_primitive(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode()
    return sha256(encoded).hexdigest()
