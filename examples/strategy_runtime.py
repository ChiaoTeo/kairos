from __future__ import annotations

import json
from tempfile import TemporaryDirectory

from kairospy.context import DataContext
from kairospy.data import DataStore
from kairospy.runtime import DataViewEventSource, StrategyRuntime
from kairospy.strategy import StrategyBase, StrategyContext


class MomentumPrinter(StrategyBase):
    strategy_id = "momentum-printer"

    def __init__(self) -> None:
        self.previous_close: float | None = None

    def on_start(self, context: StrategyContext):
        return ({"type": "strategy_started", "strategy_id": self.strategy_id},)

    def on_market(self, context: StrategyContext, event):
        close = float(event.payload["close"])
        previous = self.previous_close
        self.previous_close = close
        if previous is None:
            return ()
        if close > previous:
            signal = "up"
        elif close < previous:
            signal = "down"
        else:
            signal = "flat"
        return ({
            "type": "momentum_signal",
            "time": event.time.isoformat(),
            "close": close,
            "previous_close": previous,
            "signal": signal,
        },)

    def on_end(self, context: StrategyContext):
        return ({"type": "strategy_finished", "last_time": context.now.isoformat() if context.now else None},)


def main() -> None:
    with TemporaryDirectory() as temporary:
        store = DataStore(temporary, storage_format="jsonl")
        store.write("market.ohlcv.btc_usdt.1m", [
            {"time": "2026-01-01T00:00:00+00:00", "close": 100.0},
            {"time": "2026-01-01T00:01:00+00:00", "close": 101.5},
            {"time": "2026-01-01T00:02:00+00:00", "close": 100.8},
        ])

        data = DataContext(store)
        bars = data.attach("bars", dataset="market.ohlcv.btc_usdt.1m")
        result = StrategyRuntime(MomentumPrinter(), data).run(DataViewEventSource(bars))

    print(json.dumps({
        "strategy_id": result.strategy_id,
        "event_count": result.event_count,
        "callbacks": [record.hook for record in result.callbacks],
        "intents": list(result.intents),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
