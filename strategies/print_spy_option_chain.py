from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from kairospy.strategy import (
    ExpiryRange,
    MarketData,
    MarketId,
    OptionFilter,
    OptionRight,
    Options,
    Participant,
    ParticipantSet,
    QuoteEvent,
    Strategy,
    StrategyContext,
    StrikeRange,
)


class PrintSpyOptionChain(Strategy):
    """Print SPY and a bounded, near-the-money short-dated option window."""

    strategy_id = "print-spy-option-chain"

    def __init__(
        self,
        underlying: str = "SPY",
        max_days_to_expiry: int = 7,
        strike_offset_percent: str = "0.02",
        max_contracts: int = 40,
    ) -> None:
        self.underlying = str(underlying).upper()
        self.max_days_to_expiry = max(0, int(max_days_to_expiry))
        self.strike_offset_percent = Decimal(str(strike_offset_percent))
        if self.strike_offset_percent <= 0:
            raise ValueError("strike_offset_percent must be positive")
        self.max_contracts = max(2, int(max_contracts))
        self._underlying_scope_key = ""
        self._underlying_market_id: MarketId | None = None
        self._option_subscription_attempted = False

    def on_start(self, context: StrategyContext) -> None:
        markets = context.reference.find_markets(
            symbol=self.underlying,
            instrument_kind="equity",
        )
        market = next(iter(markets), None)
        if market is None:
            raise RuntimeError(f"no active equity market found for {self.underlying}")
        self._underlying_market_id = market.id
        self._underlying_scope_key = str(market.id)
        context.market.subscribe(
            market.id,
            data=[MarketData.QUOTE],
            participants=ParticipantSet.only(Participant.MASSIVE),
        )
        context.logger.info(
            "spy_underlying_subscribed",
            underlying=self.underlying,
            state="waiting_for_first_valid_quote",
        )

    def _subscribe_option_window(
        self,
        context: StrategyContext,
        *,
        spot: Decimal,
        observed_at: datetime,
    ) -> None:
        if self._underlying_market_id is None:
            raise RuntimeError("underlying market is not initialized")
        start, end = _expiry_window(observed_at, self.max_days_to_expiry)
        lower = spot * (Decimal("1") - self.strike_offset_percent)
        upper = spot * (Decimal("1") + self.strike_offset_percent)
        context.market.subscribe(
            Options(
                self._underlying_market_id,
                OptionFilter(
                    expiry=ExpiryRange.between_unix_nanos(start, end),
                    strike=StrikeRange.between(lower, upper),
                    right=OptionRight.BOTH,
                    limit=self.max_contracts,
                ),
            ),
            data=[MarketData.QUOTE],
            participants=ParticipantSet.only(Participant.MASSIVE),
        )
        context.logger.info(
            "spy_option_window_subscribed",
            underlying=self.underlying,
            spot=str(spot),
            expiry_from_unix_nanos=start,
            expiry_to_unix_nanos=end,
            minimum_strike=str(lower),
            maximum_strike=str(upper),
            maximum_contracts=self.max_contracts,
            strike_offset_percent=str(self.strike_offset_percent),
        )

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        quote = event.data
        scope_key = quote.scope.key()
        if scope_key == self._underlying_scope_key:
            bid = quote.bid_price
            ask = quote.ask_price
            context.logger.info(
                "spy_quote",
                symbol=self.underlying,
                bid=_price(bid),
                ask=_price(ask),
                source=quote.source_id or "unknown",
            )
            if (
                not self._option_subscription_attempted
                and bid is not None
                and ask is not None
            ):
                spot = (bid + ask) / Decimal("2")
                if spot > 0:
                    self._option_subscription_attempted = True
                    self._subscribe_option_window(
                        context,
                        spot=spot,
                        observed_at=quote.occurred_at,
                    )
            return

        if not str(quote.instrument.id).startswith("instrument:option:"):
            return
        context.logger.info(
            "spy_option_quote",
            symbol=self.underlying,
            market=quote.market_id,
            instrument=quote.instrument.id,
            bid=_price(quote.bid_price),
            ask=_price(quote.ask_price),
            bid_size=_price(quote.bid_quantity),
            ask_size=_price(quote.ask_quantity),
            scope=scope_key,
            source=quote.source_id or "unknown",
        )


def _expiry_window(observed_at: datetime, max_days: int) -> tuple[int, int]:
    observed_at = observed_at.astimezone(timezone.utc)
    start = datetime.combine(observed_at.date(), time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=max_days + 1) - timedelta(microseconds=1)
    return int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000)


def _price(value: Decimal | None) -> str:
    return "-" if value is None else str(value)
