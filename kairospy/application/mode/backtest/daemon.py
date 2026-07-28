from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Mapping

from kairospy.application.runtime.control import RunExecutionContext
from kairospy.application.runtime.source import EventSource
from kairospy.application.service.operations.run import RunAccountJournal

from .engine import BacktestEngine
from .result import BacktestResult


@dataclass(frozen=True, slots=True)
class BacktestEngineDaemonTarget:
    engine: BacktestEngine
    source: EventSource
    run_id: str | None = None

    def run(self, context: RunExecutionContext) -> dict[str, object]:
        context.heartbeat(metrics={"mode_run_status": "running"})
        journal = RunAccountJournal(
            context.control.directory,
            run_id=self.run_id or context.run_id,
            mode=context.mode.value,
        )
        self.engine.account_journal = journal
        result = self.engine.run(self.source)
        context.poll_control()
        journal.record_backtest_result(
            result,
            run_id=self.run_id or context.run_id,
            mode=context.mode.value,
        )
        summary = backtest_result_summary(result)
        if self.run_id is not None:
            summary = {"run_id": self.run_id, **summary}
        return summary


def backtest_result_summary(result: BacktestResult) -> dict[str, object]:
    return _jsonable({
        "mode": result.account.environment.value,
        "strategy_id": result.runtime.strategy_id,
        "event_count": result.runtime.event_count,
        "initial_equity": result.initial_equity,
        "final_equity": result.final_equity,
        "net_profit": result.net_profit,
        "total_return": result.total_return,
        "fills": len(result.fills),
        "closed_trades": len(result.trades),
        "metrics": result.metrics,
    })


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, StrEnum):
        return value.value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


__all__ = ["BacktestEngineDaemonTarget", "backtest_result_summary"]
