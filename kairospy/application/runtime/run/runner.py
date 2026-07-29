from __future__ import annotations

import asyncio

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel

from .session import RuntimeRunResult, RuntimeRunSession
from .spec import RuntimeRunSpec


class RuntimeRunner:
    @staticmethod
    def start(spec: RuntimeRunSpec) -> RuntimeRunSession:
        kernel = RuntimeKernel(
            spec.strategy,
            state=spec.state,
            intents=spec.intents,
            controls=spec.controls,
            views=spec.views,
        )
        return RuntimeRunSession(
            run_id=spec.run_id,
            mode=spec.mode,
            kernel=kernel,
            session=kernel.start(),
            pre_events=spec.pre_events,
            started_at=spec.started_at,
        )

    @staticmethod
    async def run(spec: RuntimeRunSpec) -> RuntimeRunResult:
        return await RuntimeRunner.start(spec).run(spec.source)

    @staticmethod
    def run_sync(spec: RuntimeRunSpec) -> RuntimeRunResult:
        return asyncio.run(RuntimeRunner.run(spec))


__all__ = ["RuntimeRunResult", "RuntimeRunSession", "RuntimeRunner", "RuntimeRunSpec"]
