"""Paper strategy that buys one bounded-risk SPY crash Put from the View."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from kairospy.application.usecases.strategy.domain.crash_put import choose_crash_put
from kairospy.application.usecases.strategy.protocol import StrategyBase
from kairospy.domain.market import Quote
from kairospy.domain.market.selection import MarketSelectionQuery
from kairospy.domain.reference import MarketRef


class MassiveSpyCrashPutStrategy(StrategyBase):
    strategy_id = "example-massive-spy-crash-put"

    def __init__(self, *, budget: Decimal | str = "1000", stress_drop: Decimal | str = "0.20") -> None:
        self.budget = Decimal(str(budget))
        self.stress_drop = Decimal(str(stress_drop))
        self.underlying = MarketRef.ephemeral(venue="massive", market="equity", source_symbol="SPY")
        self.contracts = ()
        self._submitted = False

    def on_start(self, context) -> None:
        as_of = context.now or datetime.now(timezone.utc)
        query = MarketSelectionQuery(
            venue="massive",
            market="option",
            instrument_type="option",
            limit=250,
            as_of=as_of,
        )
        selection = context.reference.query(query)
        self.contracts = context.reference.option_contracts(query)
        context.subscribe(
            self.underlying,
            selectors=(Quote.select(),),
            identity=self.strategy_id,
            params={
                "history_start": (as_of - timedelta(days=90)).date().isoformat(),
                "history_end": as_of.date().isoformat(),
                "history_timeframe": "1d",
            },
        )
        if selection.markets:
            context.subscribe(
                selection,
                selectors=(Quote.select(),),
                identity=self.strategy_id,
                params={
                    "history_start": (as_of - timedelta(days=30)).date().isoformat(),
                    "history_end": as_of.date().isoformat(),
                    "history_timeframe": "1d",
                },
            )

    def on_data(self, context, signal) -> None:
        if self._submitted or not self.contracts:
            return
        underlying_quote = context.market.quotes(self.underlying).latest
        underlying_bar = context.market.bars(self.underlying, timeframe="1d").latest
        underlying = (
            underlying_quote.midpoint
            if underlying_quote is not None
            else None if underlying_bar is None else underlying_bar.close
        )
        if underlying is None:
            return
        chain = context.option_chain(self.contracts, underlying=underlying)
        now = context.now or datetime.now(timezone.utc)
        decision = choose_crash_put(
            chain.crash_put_candidates(),
            as_of=now.date(),
            budget=self.budget,
            stress_drop=self.stress_drop,
        )
        if decision is None:
            return
        context.target_position(
            decision.contract,
            decision.quantity,
            reason=decision.reason,
            limit_price=decision.entry_price,
        )
        self._submitted = True


def strategy() -> MassiveSpyCrashPutStrategy:
    return MassiveSpyCrashPutStrategy()


__all__ = ["MassiveSpyCrashPutStrategy", "strategy"]
