from __future__ import annotations

from typing import cast

from ..source import AsyncEventSource, close_async_iterator
from .pump import RuntimeEnvelopePump
from .runner import RuntimeRunner
from .session import RuntimeRunResult
from .spec import RuntimeRunSpec


class RuntimeAsyncEnvelopeBridge:
    @staticmethod
    async def run(spec: RuntimeRunSpec) -> RuntimeRunResult:
        run_session = RuntimeRunner.start(spec)
        events = RuntimeEnvelopePump(
            spec.mode,
            pre_events=spec.pre_events,
            started_at=spec.started_at,
        ).async_events(cast(AsyncEventSource, spec.source))
        try:
            async for record in events:
                run_session.session.process(record)
        finally:
            await close_async_iterator(events)
        runtime = run_session.session.finish()
        return RuntimeRunResult(runtime, run_session.views)


__all__ = ["RuntimeAsyncEnvelopeBridge"]
