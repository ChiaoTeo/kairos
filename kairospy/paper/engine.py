from __future__ import annotations

from dataclasses import replace

from kairospy.accounts import Environment
from kairospy.backtest import BacktestEngine, BacktestResult
from kairospy.runtime import RuntimeMode


class PaperEngine:
    runtime_mode = RuntimeMode.PAPER

    def __init__(self, *args, **kwargs) -> None:
        self._engine = BacktestEngine(*args, **kwargs)
        self._engine.runtime_mode = self.runtime_mode
        self._engine.account = replace(self._engine.account, environment=Environment.PAPER)

    def run(self, *args, **kwargs) -> BacktestResult:
        return self._engine.run(*args, **kwargs)


__all__ = ["PaperEngine"]
