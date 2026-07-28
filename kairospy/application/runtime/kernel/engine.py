from __future__ import annotations

from dataclasses import dataclass

from ..model import RuntimeDataEnvelope, RuntimeStep, RuntimeStepResult, StrategyCallbackRecord, StrategyRunResult
from ..projection import RuntimeProjectionRegistry
from .dispatcher import hook_for_domain, phase_for_domain
from .output import IntentHandler, RuntimeOutputBatch, RuntimeOutputState, SubscriptionHandler
from .services import RuntimeServices
from .state import RuntimeState


@dataclass(slots=True)
class RuntimeRunFrame:
    callbacks: list[StrategyCallbackRecord]
    intents: list[object]
    output: RuntimeOutputState
    last_event: RuntimeDataEnvelope | None = None
    last_runtime_event: RuntimeDataEnvelope | None = None
    event_count: int = 0
    runtime_event_count: int = 0
    finished: bool = False

    @classmethod
    def empty(cls) -> "RuntimeRunFrame":
        return cls([], [], RuntimeOutputState())


class RuntimeEngine:
    def __init__(
        self,
        *,
        strategy,
        state: RuntimeState,
        services: RuntimeServices,
        projections: RuntimeProjectionRegistry,
    ) -> None:
        self.strategy = strategy
        self.state = state
        self.services = services
        self.projections = projections

    def start(
        self,
        *,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
    ) -> RuntimeRunFrame:
        frame = RuntimeRunFrame.empty()
        self._publish_views(frame, status="starting")
        self.projections.publish_component_views(self.state.views, last_runtime_event=None)
        context = self.services.context_factory.create(phase="start")
        returned = self.strategy.on_start(context)
        batch = self.services.output.collect_batch(context, returned, at=context.now)
        intents = batch.intents
        self._record_callback(frame, "on_start", None, intents)
        self._handle_output(frame, context, "on_start", batch, intent_handler, subscription_handler, dispatch=False)
        self._publish_views(frame, status="starting")
        return frame

    def process(
        self,
        frame: RuntimeRunFrame,
        event: RuntimeDataEnvelope,
        *,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
    ) -> RuntimeStepResult:
        envelope = self._ingest(frame, event)
        if envelope.domain == "market":
            return self._dispatch_market(frame, envelope, intent_handler, subscription_handler)
        return self._dispatch_non_market(frame, envelope, intent_handler, subscription_handler)

    def finish(
        self,
        frame: RuntimeRunFrame,
        *,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
    ) -> StrategyRunResult:
        if frame.finished:
            raise RuntimeError("strategy run frame is already finished")
        self._publish_views(frame, status="ending")
        self.projections.publish_component_views(self.state.views, last_runtime_event=frame.last_runtime_event)
        context = self.services.context_factory.create(event=frame.last_event, phase="end")
        returned = self.strategy.on_end(context)
        batch = self.services.output.collect_batch(context, returned, at=context.now)
        intents = batch.intents
        self._record_callback(frame, "on_end", None if frame.last_event is None else frame.last_event.sequence, intents)
        self._handle_output(frame, context, "on_end", batch, intent_handler, subscription_handler, dispatch=True)
        frame.finished = True
        self._publish_views(frame, status="finished")
        self.projections.publish_component_views(self.state.views, last_runtime_event=frame.last_runtime_event)
        return StrategyRunResult(
            strategy_id=self.state.strategy_id,
            event_count=frame.event_count,
            intents=tuple(frame.intents),
            callbacks=tuple(frame.callbacks),
            last_event=frame.last_event,
            runtime_event_count=frame.runtime_event_count,
            last_runtime_event=frame.last_runtime_event,
            intent_states=self.state.intents.list(strategy_id=self.state.strategy_id),
            control_requests=self.state.controls.list(strategy_id=self.state.strategy_id),
        )

    def publish_follow_up(self, frame: RuntimeRunFrame, event: RuntimeDataEnvelope, *, status: str) -> RuntimeDataEnvelope:
        envelope = self._ingest(frame, event)
        self._publish_views(frame, status=status)
        self.projections.publish_component_views(self.state.views, last_runtime_event=envelope)
        return envelope

    def _dispatch_market(
        self,
        frame: RuntimeRunFrame,
        envelope: RuntimeDataEnvelope,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
    ) -> RuntimeStepResult:
        frame.event_count += 1
        frame.last_event = envelope
        self._publish_views(frame, status="running")
        self.projections.publish_component_views(self.state.views, last_runtime_event=envelope)
        context = self.services.context_factory.create(event=envelope, phase="market")
        if context.event is None:
            raise RuntimeError("market strategy signal was not created")
        returned = self.strategy.on_market(context, context.event)
        batch = self.services.output.collect_batch(context, returned, at=context.now)
        intents = batch.intents
        follow_ups = self._handle_output(frame, context, "on_market", batch, intent_handler, subscription_handler)
        self._publish_views(frame, status="running")
        self._record_callback(frame, "on_market", envelope.sequence, intents)
        return RuntimeStepResult(
            RuntimeStep(envelope, "on_market", "market", envelope.sequence, is_market_event=True),
            intents,
            follow_ups,
        )

    def _dispatch_non_market(
        self,
        frame: RuntimeRunFrame,
        envelope: RuntimeDataEnvelope,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
    ) -> RuntimeStepResult:
        phase = phase_for_domain(envelope.domain)
        hook_name = hook_for_domain(envelope.domain)
        self._publish_views(frame, status="running")
        self.projections.publish_component_views(self.state.views, last_runtime_event=envelope)
        context = (
            self.services.context_factory.create(clock=envelope, phase=phase)
            if envelope.domain == "clock"
            else self.services.context_factory.create(event=envelope, phase=phase)
        )
        strategy_event = context.clock if envelope.domain == "clock" else context.event
        if strategy_event is None:
            raise RuntimeError(f"{hook_name} strategy signal was not created")
        returned = getattr(self.strategy, hook_name)(context, strategy_event)
        batch = self.services.output.collect_batch(context, returned, at=context.now)
        intents = batch.intents
        follow_ups = self._handle_output(frame, context, hook_name, batch, intent_handler, subscription_handler)
        self._publish_views(frame, status="running")
        self._record_callback(frame, hook_name, envelope.sequence, intents)
        return RuntimeStepResult(
            RuntimeStep(envelope, hook_name, phase, envelope.sequence),
            intents,
            follow_ups,
        )

    def _handle_output(
        self,
        frame: RuntimeRunFrame,
        context,
        hook: str,
        batch: RuntimeOutputBatch,
        intent_handler: IntentHandler | None,
        subscription_handler: SubscriptionHandler | None,
        *,
        dispatch: bool = False,
    ) -> tuple[RuntimeDataEnvelope, ...]:
        follow_ups = (
            *batch.events,
            *self.services.output.handle_intents(batch.intents, context, hook, intent_handler),
        )
        self.services.output.handle_subscription_changes(frame.output, context, hook, subscription_handler)
        if dispatch:
            for event in follow_ups:
                self.process(frame, event, intent_handler=intent_handler, subscription_handler=subscription_handler)
            return ()
        return follow_ups

    def _ingest(self, frame: RuntimeRunFrame, event: RuntimeDataEnvelope) -> RuntimeDataEnvelope:
        frame.runtime_event_count += 1
        envelope = self.state.data_pipeline.ingest(event)
        frame.last_runtime_event = envelope
        self.projections.on_event(envelope)
        return envelope

    def _publish_views(self, frame: RuntimeRunFrame, *, status: str) -> None:
        self.projections.publish_views(
            self.state.views,
            event_count=frame.event_count,
            runtime_event_count=frame.runtime_event_count,
            last_event=frame.last_event,
            last_runtime_event=frame.last_runtime_event,
            status=status,
        )

    def _record_callback(
        self,
        frame: RuntimeRunFrame,
        hook: str,
        event_sequence: int | None,
        intents: tuple[object, ...],
    ) -> None:
        frame.callbacks.append(StrategyCallbackRecord(hook, event_sequence, intents))
        frame.intents.extend(intents)


__all__ = ["RuntimeEngine", "RuntimeRunFrame"]
