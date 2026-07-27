from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from kairospy.context import DataContext
from kairospy.intents import IntentJournal, IntentState, TradeIntent
from kairospy.strategy.control import ControlJournal, ControlRequest
from kairospy.strategy.protocol import Strategy, StrategyContext
from kairospy.strategy.views import (
    ControlJournalView,
    ControlRequestSummary,
    IntentJournalView,
    IntentStateSummary,
    StrategyRunView,
    ViewStore,
)

from .components import RuntimeComponent
from .events import AccountRuntimeEvent, ClockEvent, MarketEvent, RuntimeEvent, SystemRuntimeEvent
from .market import (
    MarketAccess,
    MarketRequestService,
    MarketState,
    MarketSubscriptionRegistry,
    QuoteProvider,
)
from .sources import EventSource


@dataclass(frozen=True, slots=True)
class StrategyCallbackRecord:
    hook: str
    event_sequence: int | None
    intents: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class StrategyRunResult:
    strategy_id: str
    event_count: int
    intents: tuple[object, ...]
    callbacks: tuple[StrategyCallbackRecord, ...]
    last_event: MarketEvent | None
    runtime_event_count: int = 0
    last_runtime_event: RuntimeEvent | None = None
    intent_states: tuple[IntentState, ...] = ()
    control_requests: tuple[ControlRequest, ...] = ()


IntentHandler = Callable[[tuple[object, ...], StrategyContext, str], Iterable[RuntimeEvent] | None]


class StrategyRuntime:
    def __init__(
        self,
        strategy: Strategy,
        data: DataContext,
        *,
        state: Mapping[str, object] | None = None,
        intents: IntentJournal | None = None,
        controls: ControlJournal | None = None,
        views: ViewStore | None = None,
        components: tuple[RuntimeComponent, ...] = (),
        market_state: MarketState | None = None,
        subscriptions: MarketSubscriptionRegistry | None = None,
        quote_provider: QuoteProvider | None = None,
    ) -> None:
        if not strategy.strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy = strategy
        self.data = data
        self.state = dict(state or {})
        self.intents = intents or IntentJournal()
        self.controls = controls or ControlJournal()
        self.views = views or ViewStore()
        self.components = tuple(components)
        self.market_state = market_state or MarketState()
        self.subscriptions = subscriptions or MarketSubscriptionRegistry()
        self.quote_provider = quote_provider
        self.views.register(self.market_state.schema)
        for component in self.components:
            self.views.register(component.schema)

    def run(self, source: EventSource, *, intent_handler: IntentHandler | None = None) -> StrategyRunResult:
        records: list[StrategyCallbackRecord] = []
        all_intents: list[object] = []
        last_event: MarketEvent | None = None
        last_runtime_event: RuntimeEvent | None = None
        last_clock: ClockEvent | None = None
        event_count = 0
        runtime_event_count = 0

        self._publish_system_views(
            event_count=0,
            runtime_event_count=0,
            last_event=None,
            last_runtime_event=None,
            status="starting",
        )
        self._publish_component_views(last_runtime_event=None)
        start_context = self._context(phase="start")
        start_intents = self._collect_outputs(start_context, self.strategy.on_start(start_context), at=start_context.now)
        if intent_handler is not None:
            intent_handler(start_intents, start_context, "on_start")
        self._publish_system_views(
            event_count=0,
            runtime_event_count=0,
            last_event=None,
            last_runtime_event=None,
            status="starting",
        )
        records.append(StrategyCallbackRecord("on_start", None, start_intents))
        all_intents.extend(start_intents)

        for event in source.events():
            runtime_event_count += 1
            last_runtime_event = event
            self._publish_event_to_components(event)
            if isinstance(event, ClockEvent):
                last_clock = event
                self._publish_system_views(
                    event_count=event_count,
                    runtime_event_count=runtime_event_count,
                    last_event=last_event,
                    last_runtime_event=event,
                    status="running",
                )
                self._publish_component_views(last_runtime_event=event)
                context = self._context(clock=event, phase="clock")
                intents = self._collect_outputs(context, self.strategy.on_clock(context, event), at=context.now)
                if intent_handler is not None:
                    for follow_up in intent_handler(intents, context, "on_clock") or ():
                        runtime_event_count += 1
                        last_runtime_event = follow_up
                        self._publish_runtime_event(
                            follow_up,
                            event_count=event_count,
                            last_event=last_event,
                            status="running",
                            runtime_event_count=runtime_event_count,
                        )
                records.append(StrategyCallbackRecord("on_clock", None, intents))
                all_intents.extend(intents)
                self._publish_system_views(
                    event_count=event_count,
                    runtime_event_count=runtime_event_count,
                    last_event=last_event,
                    last_runtime_event=event,
                    status="running",
                )
                continue

            if isinstance(event, MarketEvent):
                event_count += 1
                last_event = event
                self.market_state.apply_event(event)
                self._publish_system_views(
                    event_count=event_count,
                    runtime_event_count=runtime_event_count,
                    last_event=event,
                    last_runtime_event=event,
                    status="running",
                )
                self._publish_component_views(last_runtime_event=event)
                context = self._context(event=event, phase="market")
                intents = self._collect_outputs(context, self.strategy.on_market(context, event), at=context.now)
                if intent_handler is not None:
                    for follow_up in intent_handler(intents, context, "on_market") or ():
                        runtime_event_count += 1
                        last_runtime_event = follow_up
                        self._publish_runtime_event(
                            follow_up,
                            event_count=event_count,
                            last_event=last_event,
                            status="running",
                            runtime_event_count=runtime_event_count,
                        )
                self._publish_system_views(
                    event_count=event_count,
                    runtime_event_count=runtime_event_count,
                    last_event=event,
                    last_runtime_event=last_runtime_event,
                    status="running",
                )
                records.append(StrategyCallbackRecord("on_market", event.sequence, intents))
                all_intents.extend(intents)
                continue

            if isinstance(event, (AccountRuntimeEvent, SystemRuntimeEvent)):
                self._publish_system_views(
                    event_count=event_count,
                    runtime_event_count=runtime_event_count,
                    last_event=last_event,
                    last_runtime_event=event,
                    status="running",
                )
                self._publish_component_views(last_runtime_event=event)
                continue

        self._publish_system_views(
            event_count=event_count,
            runtime_event_count=runtime_event_count,
            last_event=last_event,
            last_runtime_event=last_runtime_event,
            status="ending",
        )
        self._publish_component_views(last_runtime_event=last_runtime_event)
        end_context = self._context(event=last_event, clock=None if last_event is not None else last_clock, phase="end")
        end_intents = self._collect_outputs(end_context, self.strategy.on_end(end_context), at=end_context.now)
        if intent_handler is not None:
            for follow_up in intent_handler(end_intents, end_context, "on_end") or ():
                runtime_event_count += 1
                last_runtime_event = follow_up
                self._publish_runtime_event(
                    follow_up,
                    event_count=event_count,
                    last_event=last_event,
                    status="ending",
                    runtime_event_count=runtime_event_count,
                )
        records.append(
            StrategyCallbackRecord(
                "on_end",
                last_event.sequence if last_event else None,
                end_intents,
            )
        )
        all_intents.extend(end_intents)
        self._publish_system_views(
            event_count=event_count,
            runtime_event_count=runtime_event_count,
            last_event=last_event,
            last_runtime_event=last_runtime_event,
            status="finished",
        )
        self._publish_component_views(last_runtime_event=last_runtime_event)

        return StrategyRunResult(
            strategy_id=self.strategy.strategy_id,
            event_count=event_count,
            intents=tuple(all_intents),
            callbacks=tuple(records),
            last_event=last_event,
            runtime_event_count=runtime_event_count,
            last_runtime_event=last_runtime_event,
            intent_states=self.intents.list(strategy_id=self.strategy.strategy_id),
            control_requests=self.controls.list(strategy_id=self.strategy.strategy_id),
        )

    def _context(
        self,
        *,
        event: MarketEvent | None = None,
        clock: ClockEvent | None = None,
        phase: str,
    ) -> StrategyContext:
        return StrategyContext(
            self.data,
            event=event,
            clock=clock,
            state=self.state,
            intents=self.intents,
            controls=self.controls,
            views=self.views,
            strategy_id=self.strategy.strategy_id,
            phase=phase,
            market=MarketAccess(self.data, self.market_state),
            _subscriptions=self.subscriptions,
            _requests=MarketRequestService(
                self.data,
                self.market_state,
                phase=phase,
                quote_provider=self.quote_provider,
            ),
        )

    def _publish_system_views(
        self,
        *,
        event_count: int,
        runtime_event_count: int,
        last_event: MarketEvent | None,
        last_runtime_event: RuntimeEvent | None,
        status: str,
    ) -> None:
        as_of = _event_time(last_runtime_event) or (last_event.time if last_event is not None else None)
        self.views.put_runtime(
            "system.strategy",
            StrategyRunView(
                strategy_id=self.strategy.strategy_id,
                event_count=event_count,
                runtime_event_count=runtime_event_count,
                last_event_time=last_event.time if last_event is not None else None,
                last_stream=last_event.stream if last_event is not None else None,
                last_runtime_event_time=as_of,
                last_runtime_stream=_event_stream(last_runtime_event),
                status=status,
            ),
            as_of=as_of,
            available_time=as_of,
        )
        self.views.put_runtime("system.data", self.data.snapshot())
        self.views.put_runtime("system.intents", self._intent_view())
        self.views.put_runtime("system.control", self._control_view())
        self.views.put_runtime("market.quotes", self.market_state.view(), as_of=as_of, available_time=as_of)

    def _record_trade_intents(self, values: tuple[object, ...], *, at: datetime | None) -> None:
        occurred_at = at or datetime.now(timezone.utc)
        for value in values:
            if isinstance(value, TradeIntent):
                self.intents.record_intent(value, at=occurred_at)

    def _collect_outputs(self, context: StrategyContext, returned: object, *, at: datetime | None) -> tuple[object, ...]:
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
        self._record_trade_intents(new_values, at=at)
        return (*emitted, *new_values)

    def _publish_event_to_components(self, event: RuntimeEvent) -> None:
        for component in self.components:
            component.on_event(event)

    def _publish_runtime_event(
        self,
        event: RuntimeEvent,
        *,
        event_count: int,
        runtime_event_count: int,
        last_event: MarketEvent | None,
        status: str,
    ) -> None:
        self._publish_event_to_components(event)
        self._publish_system_views(
            event_count=event_count,
            runtime_event_count=runtime_event_count,
            last_event=last_event,
            last_runtime_event=event,
            status=status,
        )
        self._publish_component_views(last_runtime_event=event)

    def _publish_component_views(self, *, last_runtime_event: RuntimeEvent | None) -> None:
        as_of = _event_time(last_runtime_event)
        for component in self.components:
            self.views.put_runtime(component.key, component.view(), as_of=as_of, available_time=as_of)

    def _intent_view(self) -> IntentJournalView:
        states = self.intents.list(strategy_id=self.strategy.strategy_id)
        summaries = tuple(
            IntentStateSummary(
                intent_id=item.intent.intent_id,
                instrument_id=item.intent.instrument_id,
                status=item.status.value,
                active=item.active,
                updated_at=item.updated_at,
            )
            for item in states
        )
        return IntentJournalView(
            total_count=len(summaries),
            active_count=sum(1 for item in summaries if item.active),
            states=summaries,
        )

    def _control_view(self) -> ControlJournalView:
        requests = self.controls.list(strategy_id=self.strategy.strategy_id)
        summaries = tuple(
            ControlRequestSummary(
                request_id=item.request_id,
                strategy_id=item.strategy_id,
                kind=item.kind.value,
                requested_at=item.requested_at,
                payload=tuple(sorted(item.payload.items())),
                reason=item.reason,
            )
            for item in requests
        )
        return ControlJournalView(total_count=len(summaries), requests=summaries)


__all__ = ["IntentHandler", "StrategyCallbackRecord", "StrategyRunResult", "StrategyRuntime"]


def _output_tuple(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


def _event_time(event: RuntimeEvent | None) -> datetime | None:
    return None if event is None else event.time


def _event_stream(event: RuntimeEvent | None) -> str | None:
    if event is None:
        return None
    stream = getattr(event, "stream", None)
    if stream is not None:
        return str(stream)
    name = getattr(event, "name", None)
    return None if name is None else str(name)
    return None if name is None else str(name)
