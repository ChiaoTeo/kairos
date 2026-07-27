from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping

from kairospy.context import DataContext, StrategyContext
from kairospy.context.control import ControlJournal, ControlRequest
from kairospy.core.intent import IntentJournal, IntentState, TradeIntent
from kairospy.core.market import MarketSubscription, MarketSubscriptionRegistry
from kairospy.core.reference import MarketResolver
from kairospy.strategy.events import StrategySignal
from kairospy.strategy.protocol import Strategy
from kairospy.core.views import (
    ControlJournalView,
    ControlRequestSummary,
    IntentJournalView,
    IntentStateSummary,
    StrategyRunView,
    ViewStore,
)

from .components import RuntimeComponent
from .data import RuntimeDataEnvelope, RuntimeDataPipeline
from .market import (
    MarketAccess,
    MarketRequestService,
    MarketState,
    QuoteProvider,
)
from .sources import AsyncEventSource, EventSource, close_async_iterator


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
    last_event: RuntimeDataEnvelope | None
    runtime_event_count: int = 0
    last_runtime_event: RuntimeDataEnvelope | None = None
    intent_states: tuple[IntentState, ...] = ()
    control_requests: tuple[ControlRequest, ...] = ()


IntentHandler = Callable[[tuple[object, ...], StrategyContext, str], Iterable[RuntimeDataEnvelope] | None]
SubscriptionHandler = Callable[[tuple[MarketSubscription, ...], StrategyContext, str], None]


class StrategyRunSession:
    def __init__(
        self,
        runtime: "StrategyRuntime",
        state: "_RunState",
        *,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> None:
        self.runtime = runtime
        self.state = state
        self.intent_handler = intent_handler
        self.subscription_handler = subscription_handler
        self._finished = False

    def process(self, record: RuntimeDataEnvelope) -> None:
        self._ensure_active()
        self.runtime._process_record(
            self.state,
            record,
            intent_handler=self.intent_handler,
            subscription_handler=self.subscription_handler,
        )

    def finish(self) -> StrategyRunResult:
        self._ensure_active()
        self._finished = True
        return self.runtime._finish_run(
            self.state,
            intent_handler=self.intent_handler,
            subscription_handler=self.subscription_handler,
        )

    def run(self, source: EventSource) -> StrategyRunResult:
        for record in source.events():
            self.process(record)
        return self.finish()

    async def run_async(self, source: AsyncEventSource) -> StrategyRunResult:
        events = source.events()
        try:
            async for record in events:
                self.process(record)
        finally:
            await close_async_iterator(events)
        return self.finish()

    def _ensure_active(self) -> None:
        if self._finished:
            raise RuntimeError("strategy run session is already finished")


@dataclass(slots=True)
class _RunState:
    records: list[StrategyCallbackRecord]
    all_intents: list[object]
    last_event: RuntimeDataEnvelope | None = None
    last_runtime_event: RuntimeDataEnvelope | None = None
    event_count: int = 0
    runtime_event_count: int = 0
    subscription_signature: tuple[tuple[str, str, str], ...] = ()


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
        data_pipeline: RuntimeDataPipeline | None = None,
        market_state: MarketState | None = None,
        subscriptions: MarketSubscriptionRegistry | None = None,
        market_resolver: MarketResolver | None = None,
        quote_provider: QuoteProvider | None = None,
    ) -> None:
        if not strategy.strategy_id.strip():
            raise ValueError("strategy_id is required")
        self.strategy = strategy
        self.data = data
        self.market_resolver = market_resolver or getattr(data, "markets", None) or MarketResolver()
        self.state = dict(state or {})
        self.intents = intents or IntentJournal()
        self.controls = controls or ControlJournal()
        self.views = views or ViewStore()
        self.components = tuple(components)
        self.data_pipeline = data_pipeline or RuntimeDataPipeline()
        self.views.register(self.data_pipeline.schema)
        self.subscriptions = subscriptions or MarketSubscriptionRegistry()
        self.market_state = market_state or MarketState(self.subscriptions)
        if self.market_state.subscriptions is not self.subscriptions:
            self.subscriptions = self.market_state.subscriptions
        self.quote_provider = quote_provider
        for schema in self.market_state.schemas:
            self.views.register(schema)
        for component in self.components:
            self.views.register(component.schema)

    def run(
        self,
        source: EventSource,
        *,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> StrategyRunResult:
        return self.start(intent_handler=intent_handler, subscription_handler=subscription_handler).run(source)

    async def run_async(
        self,
        source: AsyncEventSource,
        *,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> StrategyRunResult:
        return await self.start(intent_handler=intent_handler, subscription_handler=subscription_handler).run_async(source)

    def start(
        self,
        *,
        intent_handler: IntentHandler | None = None,
        subscription_handler: SubscriptionHandler | None = None,
    ) -> StrategyRunSession:
        return StrategyRunSession(
            self,
            self._start_run(intent_handler=intent_handler, subscription_handler=subscription_handler),
            intent_handler=intent_handler,
            subscription_handler=subscription_handler,
        )

    def _start_run(
        self,
        *,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
    ) -> _RunState:
        state = _RunState([], [])
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
        self._handle_subscription_changes(state, start_context, "on_start", subscription_handler)
        self._publish_system_views(
            event_count=0,
            runtime_event_count=0,
            last_event=None,
            last_runtime_event=None,
            status="starting",
        )
        state.records.append(StrategyCallbackRecord("on_start", None, start_intents))
        state.all_intents.extend(start_intents)
        return state

    def _process_record(
        self,
        state: _RunState,
        record: RuntimeDataEnvelope,
        *,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
    ) -> None:
        state.runtime_event_count += 1
        envelope = self.data_pipeline.ingest(record)
        state.last_runtime_event = envelope
        self._publish_event_to_components(envelope)

        if envelope.domain == "market":
            state.event_count += 1
            state.last_event = envelope
            self.market_state.apply_envelope(envelope)
            self._publish_system_views(
                event_count=state.event_count,
                runtime_event_count=state.runtime_event_count,
                last_event=state.last_event,
                last_runtime_event=envelope,
                status="running",
            )
            self._publish_component_views(last_runtime_event=envelope)
            context = self._context(event=envelope, phase="market")
            if context.event is None:
                raise RuntimeError("market strategy signal was not created")
            intents = self._collect_outputs(context, self.strategy.on_market(context, context.event), at=context.now)
            if intent_handler is not None:
                for follow_up in intent_handler(intents, context, "on_market") or ():
                    state.runtime_event_count += 1
                    state.last_runtime_event = self._publish_runtime_event(
                        follow_up,
                        event_count=state.event_count,
                        last_event=state.last_event,
                        status="running",
                        runtime_event_count=state.runtime_event_count,
                    )
            self._handle_subscription_changes(state, context, "on_market", subscription_handler)
            self._publish_system_views(
                event_count=state.event_count,
                runtime_event_count=state.runtime_event_count,
                last_event=state.last_event,
                last_runtime_event=state.last_runtime_event,
                status="running",
            )
            state.records.append(StrategyCallbackRecord("on_market", envelope.sequence, intents))
            state.all_intents.extend(intents)
            return

        phase = _phase_for_domain(envelope.domain)
        hook_name = _hook_for_domain(envelope.domain)
        self._publish_system_views(
            event_count=state.event_count,
            runtime_event_count=state.runtime_event_count,
            last_event=state.last_event,
            last_runtime_event=envelope,
            status="running",
        )
        self._publish_component_views(last_runtime_event=envelope)
        context = self._context(clock=envelope, phase=phase) if envelope.domain == "clock" else self._context(event=envelope, phase=phase)
        strategy_event = context.clock if envelope.domain == "clock" else context.event
        if strategy_event is None:
            raise RuntimeError(f"{hook_name} strategy signal was not created")
        hook = getattr(self.strategy, hook_name)
        intents = self._collect_outputs(context, hook(context, strategy_event), at=context.now)
        if intent_handler is not None:
            for follow_up in intent_handler(intents, context, hook_name) or ():
                state.runtime_event_count += 1
                state.last_runtime_event = self._publish_runtime_event(
                    follow_up,
                    event_count=state.event_count,
                    last_event=state.last_event,
                    status="running",
                    runtime_event_count=state.runtime_event_count,
                )
        self._handle_subscription_changes(state, context, hook_name, subscription_handler)
        self._publish_system_views(
            event_count=state.event_count,
            runtime_event_count=state.runtime_event_count,
            last_event=state.last_event,
            last_runtime_event=state.last_runtime_event,
            status="running",
        )
        state.records.append(StrategyCallbackRecord(hook_name, envelope.sequence, intents))
        state.all_intents.extend(intents)

    def _finish_run(
        self,
        state: _RunState,
        *,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
    ) -> StrategyRunResult:
        self._publish_system_views(
            event_count=state.event_count,
            runtime_event_count=state.runtime_event_count,
            last_event=state.last_event,
            last_runtime_event=state.last_runtime_event,
            status="ending",
        )
        self._publish_component_views(last_runtime_event=state.last_runtime_event)
        end_context = self._context(event=state.last_event, phase="end")
        end_intents = self._collect_outputs(end_context, self.strategy.on_end(end_context), at=end_context.now)
        if intent_handler is not None:
            for follow_up in intent_handler(end_intents, end_context, "on_end") or ():
                state.runtime_event_count += 1
                state.last_runtime_event = follow_up
                self._publish_runtime_event(
                    follow_up,
                    event_count=state.event_count,
                    last_event=state.last_event,
                    status="ending",
                    runtime_event_count=state.runtime_event_count,
                )
        self._handle_subscription_changes(state, end_context, "on_end", subscription_handler)
        state.records.append(
            StrategyCallbackRecord(
                "on_end",
                state.last_event.sequence if state.last_event else None,
                end_intents,
            )
        )
        state.all_intents.extend(end_intents)
        self._publish_system_views(
            event_count=state.event_count,
            runtime_event_count=state.runtime_event_count,
            last_event=state.last_event,
            last_runtime_event=state.last_runtime_event,
            status="finished",
        )
        self._publish_component_views(last_runtime_event=state.last_runtime_event)

        return StrategyRunResult(
            strategy_id=self.strategy.strategy_id,
            event_count=state.event_count,
            intents=tuple(state.all_intents),
            callbacks=tuple(state.records),
            last_event=state.last_event,
            runtime_event_count=state.runtime_event_count,
            last_runtime_event=state.last_runtime_event,
            intent_states=self.intents.list(strategy_id=self.strategy.strategy_id),
            control_requests=self.controls.list(strategy_id=self.strategy.strategy_id),
        )

    def _handle_subscription_changes(
        self,
        state: _RunState,
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

    def _context(
        self,
        *,
        event: RuntimeDataEnvelope | None = None,
        clock: RuntimeDataEnvelope | None = None,
        phase: str,
    ) -> StrategyContext:
        strategy_event = None if event is None else _strategy_signal(event)
        strategy_clock = None if clock is None else _strategy_signal(clock)
        return StrategyContext(
            self.data,
            event=strategy_event,
            clock=strategy_clock,
            state=self.state,
            intents=self.intents,
            controls=self.controls,
            views=self.views,
            strategy_id=self.strategy.strategy_id,
            phase=phase,
            market=MarketAccess(self.market_resolver, self.market_state),
            market_resolver=self.market_resolver,
            _subscriptions=self.subscriptions,
            _requests=MarketRequestService(
                self.market_resolver,
                self.market_state,
                phase=phase,
                quote_provider=self.quote_provider,
            ),
            dataflow=self.data_pipeline,
        )

    def _publish_system_views(
        self,
        *,
        event_count: int,
        runtime_event_count: int,
        last_event: RuntimeDataEnvelope | None,
        last_runtime_event: RuntimeDataEnvelope | None,
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
        self.views.put_runtime("system.dataflow", self.data_pipeline.view(), as_of=as_of, available_time=as_of)
        self.views.put_runtime("system.intents", self._intent_view())
        self.views.put_runtime("system.control", self._control_view())
        self.views.put_runtime("market.subscriptions", self.market_state.subscriptions_view(), as_of=as_of, available_time=as_of)
        self.views.put_runtime("market.quotes", self.market_state.quotes_view(), as_of=as_of, available_time=as_of)
        self.views.put_runtime("market.rates", self.market_state.rates_view(), as_of=as_of, available_time=as_of)
        self.views.put_runtime("market.books", self.market_state.books_view(), as_of=as_of, available_time=as_of)
        self.views.put_runtime("market.bars", self.market_state.bars_view(), as_of=as_of, available_time=as_of)
        self.views.put_runtime("market.trades", self.market_state.trades_view(), as_of=as_of, available_time=as_of)
        self.views.put_runtime("market.observations", self.market_state.observations_view(), as_of=as_of, available_time=as_of)
        self.views.put_runtime("market.fields", self.market_state.fields_view(), as_of=as_of, available_time=as_of)

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

    def _publish_event_to_components(self, event: RuntimeDataEnvelope) -> None:
        for component in self.components:
            component.on_event(event)

    def _publish_runtime_event(
        self,
        event: RuntimeDataEnvelope,
        *,
        event_count: int,
        runtime_event_count: int,
        last_event: RuntimeDataEnvelope | None,
        status: str,
    ) -> RuntimeDataEnvelope:
        envelope = self.data_pipeline.ingest(event)
        self._publish_event_to_components(envelope)
        self._publish_system_views(
            event_count=event_count,
            runtime_event_count=runtime_event_count,
            last_event=last_event,
            last_runtime_event=envelope,
            status=status,
        )
        self._publish_component_views(last_runtime_event=envelope)
        return envelope

    def _publish_component_views(self, *, last_runtime_event: RuntimeDataEnvelope | None) -> None:
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


__all__ = [
    "IntentHandler",
    "StrategyCallbackRecord",
    "StrategyRunResult",
    "StrategyRunSession",
    "StrategyRuntime",
    "SubscriptionHandler",
]


def _output_tuple(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Mapping)):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


def _strategy_signal(event: RuntimeDataEnvelope) -> StrategySignal:
    return StrategySignal(
        domain=str(event.domain),
        kind=event.kind,
        time=event.time,
        sequence=event.sequence,
        stream=event.stream,
        source=event.source,
        metadata=event.metadata,
    )


def _event_time(event: RuntimeDataEnvelope | None) -> datetime | None:
    return None if event is None else event.time


def _event_stream(event: RuntimeDataEnvelope | None) -> str | None:
    if event is None:
        return None
    return event.stream or None


def _subscription_signature(subscriptions: tuple[MarketSubscription, ...]) -> tuple[tuple[str, str, str], ...]:
    return tuple(sorted((item.key, item.status, item.error) for item in subscriptions))


def _phase_for_domain(domain: object) -> str:
    if domain == "execution":
        return "order"
    return str(domain)


def _hook_for_domain(domain: object) -> str:
    hooks = {
        "account": "on_account",
        "clock": "on_clock",
        "execution": "on_order",
        "system": "on_system",
    }
    return hooks.get(str(domain), "on_system")
