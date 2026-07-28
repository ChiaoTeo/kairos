from __future__ import annotations

from typing import cast

from kairospy.core.reference import MarketResolver

from ..kernel import RuntimeKernel, RuntimeQueue
from ..source import AsyncEventSource, EventSource
from .session import RuntimeRunResult, RuntimeRunSession
from .spec import RuntimeRunSpec


class RuntimeRunner:
    @staticmethod
    def start(spec: RuntimeRunSpec) -> RuntimeRunSession:
        strategy_runtime = _strategy_runtime(spec)
        services = spec.service_config
        session = strategy_runtime.start(
            intent_handler=services.intent_handler,
            subscription_handler=services.subscription_handler,
        )
        return RuntimeRunSession(
            strategy_runtime,
            session,
            spec.mode,
            pre_events=spec.pre_events,
            started_at=spec.started_at,
        )

    @staticmethod
    def run(spec: RuntimeRunSpec) -> RuntimeRunResult:
        run_session = RuntimeRunner.start(spec)
        for record in RuntimeQueue(
            spec.mode,
            _sync_source(spec.source).events(),
            pre_events=spec.pre_events,
            started_at=spec.started_at,
        ).events():
            run_session.session.process(record)
        runtime = run_session.session.finish()
        return RuntimeRunResult(runtime, run_session.views)


def _strategy_runtime(spec: RuntimeRunSpec) -> RuntimeKernel:
    state = spec.state_config
    services = spec.service_config
    projections = spec.projection_config
    return RuntimeKernel(
        spec.strategy,
        state.data,
        components=projections.components,
        market_resolver=state.market_resolver or MarketResolver(),
        request_providers=services.request_providers,
    )


def _sync_source(source: EventSource | AsyncEventSource) -> EventSource:
    return cast(EventSource, source)


__all__ = ["RuntimeRunResult", "RuntimeRunSession", "RuntimeRunner", "RuntimeRunSpec"]
