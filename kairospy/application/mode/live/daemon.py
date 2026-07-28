from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.application.runtime.control import RunDaemonPhase, RunExecutionContext
from kairospy.application.service.operations.run import RunAccountJournal

from .engine import LiveEngine, LiveSourceFactory
from .result import LiveLoopHeartbeat, LiveLoopIteration, LiveLoopResult, LiveStopToken


@dataclass(frozen=True, slots=True)
class LiveEngineDaemonTarget:
    engine: LiveEngine
    source_factory: LiveSourceFactory
    symbol: str | None = None
    balance_params: Mapping[str, object] | None = None
    order_params: Mapping[str, object] | None = None
    max_balance_events: int = 0
    max_order_events: int = 0
    max_trade_events: int = 0
    max_iterations: int | None = None
    retry_backoff_seconds: float = 1.0
    max_consecutive_failures: int | None = None
    stop_drain_timeout_seconds: float = 30.0

    def run(self, context: RunExecutionContext) -> dict[str, object]:
        token = LiveStopToken()
        journal = RunAccountJournal(context.control.directory)
        monitor = _DaemonLiveLoopMonitor(context, token)
        result = self.engine.run_loop(
            self.source_factory,
            symbol=self.symbol,
            balance_params=self.balance_params,
            order_params=self.order_params,
            max_balance_events=self.max_balance_events,
            max_order_events=self.max_order_events,
            max_trade_events=self.max_trade_events,
            max_iterations=self.max_iterations,
            stop=lambda iteration: _stop_after_iteration(context, token, iteration, journal),
            stop_token=token,
            monitor=monitor,
            retry_backoff_seconds=self.retry_backoff_seconds,
            max_consecutive_failures=self.max_consecutive_failures,
            stop_drain_timeout_seconds=self.stop_drain_timeout_seconds,
        )
        context.poll_control()
        latest = result.latest
        if latest is not None:
            journal.record_account_view(
                latest.account_view,
                run_id=context.run_id,
                mode=context.mode.value,
            )
        return _loop_result(result)


class _DaemonLiveLoopMonitor:
    def __init__(self, context: RunExecutionContext, token: LiveStopToken) -> None:
        self.context = context
        self.token = token

    def heartbeat(self, event: LiveLoopHeartbeat) -> None:
        if self.context.poll_control():
            self.token.request_stop(self.context.stop_reason)
        phase = RunDaemonPhase.STOPPING if event.status in {"draining", "stopped"} else RunDaemonPhase.RUNNING
        reason = event.stop_reason or event.error or event.status
        self.context.heartbeat(
            phase,
            reason,
            metrics={
                "live_loop_status": event.status,
                "iteration": event.iteration,
                "account": event.account.account.value,
                "consecutive_failures": event.consecutive_failures,
            },
        )


def _stop_after_iteration(
    context: RunExecutionContext,
    token: LiveStopToken,
    iteration: LiveLoopIteration,
    journal: RunAccountJournal,
) -> bool:
    if iteration.result is not None:
        journal.record_account_view(
            iteration.result.account_view,
            run_id=context.run_id,
            mode=context.mode.value,
        )
    if context.poll_control():
        token.request_stop(context.stop_reason)
    return token.requested


def _loop_result(result: LiveLoopResult) -> dict[str, object]:
    latest = result.latest
    return {
        "iterations": len(result.iterations),
        "succeeded_count": result.succeeded_count,
        "incident_count": len(result.incidents),
        "latest_strategy_id": latest.runtime.strategy_id if latest is not None else None,
        "latest_event_count": latest.runtime.event_count if latest is not None else None,
    }


__all__ = ["LiveEngineDaemonTarget"]
