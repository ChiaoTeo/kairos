from __future__ import annotations

from kairospy.application.usecases.strategy.cli import CliStrategyBase


class CliSystemCheckStrategy(CliStrategyBase):
    strategy_id = "cli-system-check"


def strategy() -> CliSystemCheckStrategy:
    return CliSystemCheckStrategy()
