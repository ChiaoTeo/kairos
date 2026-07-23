from __future__ import annotations

from typing import Any, Sequence

from kairospy.strategy.intents import Intent
from kairospy.strategy.protocols import Context


class PrintMarketStrategy:
    strategy_id = "examples-print-market-v1"

    def on_start(self, context: Context) -> Sequence[Intent]:
        return ()

    def on_market(self, context: Context) -> Sequence[Intent]:
        if context.market.top_of_book:
            for book in context.market.top_of_book:
                spread = book.spread
                print(
                    f"[book {context.market.sequence:03d}] "
                    f"{book.instrument_id} "
                    f"bid={book.bid} bid_size={book.bid_size} "
                    f"ask={book.ask} ask_size={book.ask_size} "
                    f"spread={spread} "
                    f"binding={context.market.data_binding}"
                )
            return ()
        print(
            f"[market {context.market.sequence:03d}] "
            f"binding={context.market.data_binding} "
            f"instruments={tuple(str(item) for item in context.market.instruments)}"
        )
        return ()

    def on_fill(self, fill: Any, context: Context) -> Sequence[Intent]:
        return ()

    def on_end(self, context: Context) -> Sequence[Intent]:
        return ()


def build() -> PrintMarketStrategy:
    return PrintMarketStrategy()
