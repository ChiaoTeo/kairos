from __future__ import annotations

from typing import Any, Sequence

from kairospy.strategy.intents import Intent
from kairospy.strategy.protocols import Context, StrategyDecision


REQUIRES = {
    "inputs": {
        "market": "Historical market rows to print.",
    }
}


class PrintMarketStrategy:
    strategy_id = "examples-print-market-v1"

    def __init__(self, workspace: Any, params: dict[str, str]) -> None:
        self.binding = params.get("data", "market")
        self.limit = int(params.get("limit", "100"))
        self.rows = workspace.data.get(self.binding).collect("rows")[:self.limit]
        self._decisions: list[StrategyDecision] = []

    @property
    def decisions(self) -> tuple[StrategyDecision, ...]:
        return tuple(self._decisions)

    def on_start(self, context: Context) -> Sequence[Intent]:
        self._record(context, "start", "printer strategy started")
        return ()

    def on_market(self, context: Context) -> Sequence[Intent]:
        index = context.market.sequence - 1
        row = self.rows[index] if 0 <= index < len(self.rows) else {}
        print(f"[market {context.market.sequence:03d}] {_string_row(row)}")
        self._record(context, "print_market", f"printed market row {context.market.sequence}")
        return ()

    def on_fill(self, fill: Any, context: Context) -> Sequence[Intent]:
        return ()

    def on_end(self, context: Context) -> Sequence[Intent]:
        self._record(context, "end", "printer strategy ended")
        return ()

    def _record(self, context: Context, action: str, reason: str) -> None:
        candidates = tuple(str(item) for item in context.market.instruments)
        self._decisions.append(StrategyDecision(str(context.now), action, reason, candidates))


def build(workspace: Any, params: dict[str, str]) -> PrintMarketStrategy:
    return PrintMarketStrategy(workspace, params)


def _string_row(row: dict[str, object]) -> dict[str, str]:
    return {key: str(value) for key, value in row.items()}
