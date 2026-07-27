from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from kairospy.runtime.daemon import LiveRunDaemonPhase, LiveRunExecutionContext

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

    def run(self, context: LiveRunExecutionContext) -> dict[str, object]:
        token = LiveStopToken()
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
            stop=lambda iteration: _stop_after_iteration(context, token, iteration),
            stop_token=token,
            monitor=monitor,
            retry_backoff_seconds=self.retry_backoff_seconds,
            max_consecutive_failures=self.max_consecutive_failures,
        )
        context.poll_control()
        return _loop_result(result)


class _DaemonLiveLoopMonitor:
    def __init__(self, context: LiveRunExecutionContext, token: LiveStopToken) -> None:
        self.context = context
        self.token = token

    def heartbeat(self, event: LiveLoopHeartbeat) -> None:
        if self.context.poll_control():
            self.token.request_stop(self.context.stop_reason)
        phase = LiveRunDaemonPhase.RUNNING if event.status != "stopped" else LiveRunDaemonPhase.STOPPING
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
    context: LiveRunExecutionContext,
    token: LiveStopToken,
    iteration: LiveLoopIteration,
) -> bool:
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
