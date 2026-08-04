"""Strategy-specific adapter for the generic runtime engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from kairospy.application.support.runtime.application.dispatch.dispatcher import RuntimeDispatcherPort
from kairospy.application.support.runtime.application.engine import Callback, RuntimeEngineSpec, RuntimeFrame, RuntimeResult, create_runtime_session
from kairospy.application.support.messaging import Message
from kairospy.application.usecases.strategy.application.context import StrategyContext
from kairospy.application.usecases.strategy.protocol import Strategy


class StrategyRuntimeDispatcher:
    """Maps generic runtime frames to Strategy usecase callbacks."""

    def __init__(
        self,
        strategy: Strategy,
        *,
        context: StrategyContext,
    ) -> None:
        self.strategy = strategy
        self.context = context

    def start(self, frame: RuntimeFrame) -> None:
        self._call(frame, "on_start", self.context.bind(None))

    def process(self, frame: RuntimeFrame, event: Message, *, hook: str | None) -> object | None:
        self._ensure_active(frame)
        frame.event_count += 1
        frame.last_event = event
        context = self.context.bind(event)
        if hook is None:
            frame.callbacks.append(Callback("runtime", event.sequence))
            return None
        self._call(frame, hook, context, event, event=event)
        return StrategyCallbackResult(
            hook=hook,
            intents=self.context.emitted_intents,
            context=self.context,
        )

    def finish(self, frame: RuntimeFrame) -> RuntimeResult:
        self._ensure_active(frame)
        self._call(frame, "on_end", self.context.bind(frame.last_event))
        frame.finished = True
        return RuntimeResult(
            program_id=self.strategy.strategy_id,
            event_count=frame.event_count,
            callbacks=tuple(frame.callbacks),
            last_event=frame.last_event,
        )

    def _call(
        self,
        frame: RuntimeFrame,
        hook: str,
        context: StrategyContext,
        *args: object,
        event: Message | None = None,
    ) -> None:
        returned = getattr(self.strategy, hook)(context, *args)
        if returned is not None:
            raise TypeError(f"{hook} must return None; emit intents with context.intent()")
        frame.callbacks.append(Callback(hook, None if event is None else event.sequence))

    @staticmethod
    def _ensure_active(frame: RuntimeFrame) -> None:
        if frame.finished:
            raise RuntimeError("runtime session is already finished")


def build_strategy_dispatcher(
    strategy: Strategy,
    *,
    state: dict[str, object],
    system_call: object | None,
    views: object,
    reference: object | None,
) -> RuntimeDispatcherPort:
    context = StrategyContext(
        strategy_id=strategy.strategy_id,
        state=state,
        system_call=system_call,
        views=views,
        reference=reference,
    )
    return StrategyRuntimeDispatcher(strategy, context=context)


def build_strategy_runtime_session(strategy: Strategy, **kwargs: object):
    """Build a public runtime session for one Strategy."""
    return create_runtime_session(
        RuntimeEngineSpec(
            program_id=str(strategy.strategy_id),
            dispatcher_factory=lambda **dispatcher_kwargs: build_strategy_dispatcher(strategy, **dispatcher_kwargs),
            **kwargs,
        )
    )


@dataclass(frozen=True, slots=True)
class StrategyCallbackResult:
    """Strategy-owned callback output interpreted by System, not Runtime."""

    hook: str
    intents: tuple[object, ...]
    context: StrategyContext


__all__ = ["StrategyCallbackResult", "StrategyRuntimeDispatcher", "build_strategy_dispatcher", "build_strategy_runtime_session"]
