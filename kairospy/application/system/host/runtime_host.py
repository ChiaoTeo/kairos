from __future__ import annotations

import asyncio

from kairospy.application.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.runtime.ports import AccountPort, TradingExecutionPort
from kairospy.application.runtime.run import RuntimeRunResult, RuntimeRunSession
from kairospy.application.system.artifacts.journals.account import RunAccountJournal
from kairospy.core.execution import ExecutionCoordinator

from .lifecycle import NoopTradingLifecycle
from .resources import TradingRuntimeResources, TradingRunSpec


class TradingSystem:
    def __init__(self, spec: TradingRunSpec) -> None:
        self.spec = spec

    def run(self) -> RuntimeRunResult:
        resources = self.spec.resources
        lifecycle = self.spec.lifecycle or NoopTradingLifecycle()
        lifecycle.prepare()
        connections_started = False
        if resources.connections is not None:
            resources.connections.start()
            connections_started = True
        try:
            kernel = RuntimeKernel(
                self.spec.strategy,
                data=resources.data,
                account=resources.account,
                reference=resources.reference,
                trading_execution=resources.trading_execution,
                execution_coordinator=self._execution_coordinator(resources),
                account_journal=RunAccountJournal(self.spec.run_directory, run_id=self.spec.run_id, mode=self.spec.mode.value),
            )
            session = RuntimeRunSession(
                run_id=self.spec.run_id,
                mode=self.spec.mode,
                kernel=kernel,
                session=kernel.start(),
            )
            result = asyncio.run(session.run(resources.source))
            lifecycle.complete()
            return result
        finally:
            if connections_started and resources.connections is not None:
                resources.connections.stop()

    def _execution_coordinator(self, resources: TradingRuntimeResources) -> ExecutionCoordinator | None:
        for candidate in (resources.trading_execution, resources.account):
            coordinator = _coordinator(candidate)
            if coordinator is not None:
                return coordinator
        return None


def _coordinator(candidate: TradingExecutionPort | AccountPort | None) -> ExecutionCoordinator | None:
    value = getattr(candidate, "coordinator", None)
    if isinstance(value, ExecutionCoordinator):
        return value
    return None


__all__ = ["TradingSystem"]
