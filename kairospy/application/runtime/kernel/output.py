from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from kairospy.application.context import StrategyContext
from kairospy.core.intent import IntentJournal, TradeIntent
from kairospy.application.service.domains.market import MarketSubscription, MarketSubscriptionRegistry

from ..model import RuntimeDataEnvelope


IntentHandler = Callable[[tuple[object, ...], StrategyContext, str], Iterable[RuntimeDataEnvelope] | None]
SubscriptionHandler = Callable[[tuple[MarketSubscription, ...], StrategyContext, str], None]


@dataclass(slots=True)
class RuntimeOutputState:
    subscription_signature: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeOutputBatch:
    intents: tuple[object, ...] = ()
    events: tuple[RuntimeDataEnvelope, ...] = ()


class RuntimeOutputProcessor:
    def __init__(
        self,
        *,
        strategy_id: str,
        intents: IntentJournal,
        subscriptions: MarketSubscriptionRegistry,
    ) -> None:
        self.strategy_id = strategy_id
        self.intents = intents
        self.subscriptions = subscriptions

    def collect(self, context: StrategyContext, returned: object, *, at: datetime | None) -> tuple[object, ...]:
        return self.collect_batch(context, returned, at=at).intents

    def collect_batch(self, context: StrategyContext, returned: object, *, at: datetime | None) -> RuntimeOutputBatch:
        emitted = tuple(context._emitted_intents)
        emitted_ids = {intent.intent_id for intent in emitted}
        returned_values = _output_tuple(returned)
        new_values = tuple(
            value
            for value in returned_values
            if not (isinstance(value, TradeIntent) and value.intent_id in emitted_ids)
        )
        returned_trade_intents = tuple(value for value in new_values if isinstance(value, TradeIntent))
        if returned_trade_intents:
            raise TypeError("TradeIntent must be emitted with context.target_position(), not returned from a strategy hook")
        self.record_trade_intents(new_values, at=at)
        return RuntimeOutputBatch((*emitted, *new_values), tuple(context._emitted_events))

    def record_trade_intents(self, values: tuple[object, ...], *, at: datetime | None) -> None:
        occurred_at = at or datetime.now(timezone.utc)
        for value in values:
            if isinstance(value, TradeIntent):
                self.intents.record_intent(value, at=occurred_at)

    def handle_intents(
        self,
        values: tuple[object, ...],
        context: StrategyContext,
        hook: str,
        intent_handler: IntentHandler | None,
    ) -> tuple[RuntimeDataEnvelope, ...]:
        if intent_handler is None:
            return ()
        return tuple(intent_handler(values, context, hook) or ())

    def handle_subscription_changes(
        self,
        state: RuntimeOutputState,
        context: StrategyContext,
        hook: str,
        subscription_handler: SubscriptionHandler | None,
    ) -> None:
        signature = _subscription_signature(self.subscriptions.list())
        if signature == state.subscription_signature:
            return
        state.subscription_signature = signature
        if subscription_handler is not None:
            subscription_handler(self.subscriptions.list(), context, hook)


def _output_tuple(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


def _subscription_signature(subscriptions: tuple[MarketSubscription, ...]) -> tuple[tuple[str, str, str], ...]:
    return tuple(sorted((item.key, item.status, item.error) for item in subscriptions))


__all__ = [
    "IntentHandler",
    "RuntimeOutputBatch",
    "RuntimeOutputProcessor",
    "RuntimeOutputState",
    "SubscriptionHandler",
]
