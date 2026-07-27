from __future__ import annotations

from dataclasses import dataclass

from kairospy.modes.backtest import BacktestResult
from kairospy.modes.backtest.daemon import backtest_result_summary
from kairospy.runtime import EventSource
from kairospy.runtime.account_journal import RunAccountJournal
from kairospy.runtime.daemon import RunExecutionContext

from .engine import PaperEngine


@dataclass(frozen=True, slots=True)
class PaperEngineDaemonTarget:
    engine: PaperEngine
    source: EventSource
    run_id: str | None = None

    def run(self, context: RunExecutionContext) -> dict[str, object]:
        context.heartbeat(metrics={"mode_run_status": "running"})
        journal = RunAccountJournal(
            context.control.directory,
            run_id=self.run_id or context.run_id,
            mode=context.mode.value,
        )
        self.engine._engine.account_journal = journal
        result = self.engine.run(self.source)
        context.poll_control()
        journal.record_backtest_result(
            result,
            run_id=self.run_id or context.run_id,
            mode=context.mode.value,
        )
        summary = paper_result_summary(result)
        if self.run_id is not None:
            summary = {"run_id": self.run_id, **summary}
        return summary


def paper_result_summary(result: BacktestResult) -> dict[str, object]:
    return backtest_result_summary(result)


__all__ = ["PaperEngineDaemonTarget", "paper_result_summary"]
