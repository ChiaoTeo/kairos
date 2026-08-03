from __future__ import annotations

import asyncio

from kairospy.application.support.runtime.services.orchestration.kernel import RuntimeKernel
from kairospy.application.support.runtime.services.orchestration.state import RuntimeStores
from kairospy.application.support.runtime.domain.components import RuntimeComponents

from .session import RuntimeLaunchResult, RuntimeLaunchSession
from .spec import RuntimeLaunchSpec


class RuntimeRunner:
    @staticmethod
    def start(spec: RuntimeLaunchSpec) -> RuntimeLaunchSession:
        kernel = RuntimeKernel(
            spec.strategy,
            components=RuntimeComponents(),
            stores=spec.stores or RuntimeStores(),
            processors=spec.processors,
        )
        return RuntimeLaunchSession(
            launch_id=spec.launch_id,
            mode=spec.mode,
            kernel=kernel,
            session=kernel.start(),
            pre_events=spec.pre_events,
            started_at=spec.started_at,
        )

    @staticmethod
    async def run(spec: RuntimeLaunchSpec) -> RuntimeLaunchResult:
        return await RuntimeRunner.start(spec).run(spec.source)

    @staticmethod
    def run_sync(spec: RuntimeLaunchSpec) -> RuntimeLaunchResult:
        return asyncio.run(RuntimeRunner.run(spec))


__all__ = ["RuntimeLaunchResult", "RuntimeLaunchSession", "RuntimeRunner", "RuntimeLaunchSpec"]
