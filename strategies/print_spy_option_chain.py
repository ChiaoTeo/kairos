from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from kairospy.strategy import QuoteEvent, Strategy, StrategyContext


class PrintSpyOptionChain(Strategy):
    """Print SPY and a bounded, near-the-money short-dated option window."""

    strategy_id = "print-spy-option-chain"

    def __init__(
        self,
        underlying: str = "SPY",
        max_days_to_expiry: int = 7,
        strike_offset_percent: str = "0.02",
        max_expiries: int = 3,
        max_contracts: int = 40,
    ) -> None:
        self.underlying = str(underlying).upper()
        self.max_days_to_expiry = max(0, int(max_days_to_expiry))
        self.strike_offset_percent = Decimal(str(strike_offset_percent))
        if self.strike_offset_percent <= 0:
            raise ValueError("strike_offset_percent must be positive")
        self.max_expiries = max(1, int(max_expiries))
        self.max_contracts = max(2, int(max_contracts))
        self._underlying_market_id = ""
        self._underlying_instrument_id = ""
        self._option_subscription_attempted = False
        self._option_details: dict[str, tuple[str, str, str]] = {}

    def on_start(self, context: StrategyContext) -> None:
        underlying_markets = context.reference.find_markets(
            symbol=self.underlying,
            market_type="equity",
        )
        underlying_market = next(
            (
                market
                for market in underlying_markets
                if str(market.exchange_id) != "exchange:binance"
            ),
            None,
        )
        if underlying_market is None:
            raise RuntimeError(
                f"no active Massive equity market found for {self.underlying}"
            )
        self._underlying_market_id = str(underlying_market.id)
        self._underlying_instrument_id = str(underlying_market.instrument.id)
        context.market.subscribe_quotes(underlying_market)
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
        start, end = _expiry_window(observed_at, self.max_days_to_expiry)
        chain = context.reference.option_chain(
            self._underlying_instrument_id,
            expiry_from_unix_nanos=start,
            expiry_to_unix_nanos=end,
            limit=5_000,
        )
        selected_chain = _select_contracts(
            chain,
            spot=spot,
            strike_offset_percent=self.strike_offset_percent,
            max_expiries=self.max_expiries,
            max_contracts=self.max_contracts,
        )
        if not selected_chain:
            lower = spot * (Decimal("1") - self.strike_offset_percent)
            upper = spot * (Decimal("1") + self.strike_offset_percent)
            raise RuntimeError(
                f"no active {self.underlying} options expire within "
                f"{self.max_days_to_expiry} days with strikes in [{lower}, {upper}]"
            )

        contracts = {str(instrument.id): instrument for instrument in selected_chain}
        market_ids = tuple(
            _massive_option_market_id(self.underlying, instrument)
            for instrument in contracts.values()
        )
        option_markets = context.reference.find_markets(
            market_ids=market_ids,
            limit=max(1, len(market_ids)),
        )
        option_markets = tuple(
            market
            for market in option_markets
            if str(market.id).startswith("market:massive:options:")
            and str(market.instrument.id) in contracts
        )
        if not option_markets:
            raise RuntimeError(
                f"no Massive option markets found for the selected {self.underlying} contracts"
            )

        for market in option_markets:
            instrument = contracts[str(market.instrument.id)]
            expiry = instrument.expiry_unix_nanos
            if expiry is None:
                continue
            self._option_details[str(market.id)] = (
                str(instrument.option_right or "-").upper(),
                str(instrument.strike or "-"),
                _utc_date(expiry),
            )
            context.market.subscribe_quotes(market)

        expiries = sorted(
            {
                _utc_date(instrument.expiry_unix_nanos)
                for instrument in selected_chain
                if instrument.expiry_unix_nanos is not None
            }
        )
        strikes = [Decimal(str(instrument.strike)) for instrument in selected_chain]
        context.logger.info(
            "spy_option_chain_subscribed",
            underlying=self.underlying,
            spot=str(spot),
            expiries=expiries,
            option_contracts=len(option_markets),
            available_contracts=len(chain),
            minimum_strike=str(min(strikes)),
            maximum_strike=str(max(strikes)),
            strike_offset_percent=str(self.strike_offset_percent),
        )

    def on_quote(self, context: StrategyContext, event: QuoteEvent) -> None:
        quote = event.data
        market_id = str(quote.market_id)
        if market_id == self._underlying_market_id:
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

        details = self._option_details.get(market_id)
        if details is None:
            return
        right, strike, expiry = details
        context.logger.info(
            "spy_option_quote",
            symbol=self.underlying,
            expiry=expiry,
            right=right,
            strike=strike,
            bid=_price(quote.bid_price),
            ask=_price(quote.ask_price),
            bid_size=_price(quote.bid_quantity),
            ask_size=_price(quote.ask_quantity),
            market_id=market_id,
        )


def _utc_date(unix_nanos: int) -> str:
    return (
        datetime.fromtimestamp(unix_nanos / 1_000_000_000, tz=timezone.utc)
        .date()
        .isoformat()
    )


def _massive_option_market_id(underlying: str, instrument: object) -> str:
    expiry_unix_nanos = getattr(instrument, "expiry_unix_nanos", None)
    strike = getattr(instrument, "strike", None)
    right = str(getattr(instrument, "option_right", "") or "").upper()
    if expiry_unix_nanos is None or strike is None or right[:1] not in {"C", "P"}:
        raise RuntimeError(f"incomplete option contract: {instrument}")
    expiry = datetime.fromtimestamp(
        expiry_unix_nanos / 1_000_000_000, tz=timezone.utc
    ).strftime("%y%m%d")
    strike_code = int(Decimal(str(strike)) * 1_000)
    ticker = f"O:{underlying}{expiry}{right[:1]}{strike_code:08d}"
    return f"market:massive:options:{ticker}"


def _expiry_window(observed_at: datetime, max_days: int) -> tuple[int, int]:
    observed_at = observed_at.astimezone(timezone.utc)
    start = datetime.combine(observed_at.date(), time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=max_days + 1) - timedelta(microseconds=1)
    return int(start.timestamp() * 1_000_000_000), int(end.timestamp() * 1_000_000_000)


def _select_contracts(
    chain: tuple[object, ...],
    *,
    spot: Decimal,
    strike_offset_percent: Decimal,
    max_expiries: int,
    max_contracts: int,
) -> tuple[object, ...]:
    lower = spot * (Decimal("1") - strike_offset_percent)
    upper = spot * (Decimal("1") + strike_offset_percent)
    eligible = tuple(
        instrument
        for instrument in chain
        if getattr(instrument, "expiry_unix_nanos", None) is not None
        and getattr(instrument, "strike", None) is not None
        and str(getattr(instrument, "option_right", "") or "").upper()
        in {"CALL", "PUT"}
        and lower <= Decimal(str(getattr(instrument, "strike"))) <= upper
    )
    expiries = sorted(
        {int(getattr(instrument, "expiry_unix_nanos")) for instrument in eligible}
    )[:max_expiries]
    if not expiries:
        return ()

    selected: list[object] = []
    remaining_expiries = len(expiries)
    for expiry in expiries:
        expiry_budget = max(2, (max_contracts - len(selected)) // remaining_expiries)
        per_right = max(1, expiry_budget // 2)
        for right in ("CALL", "PUT"):
            candidates = sorted(
                (
                    instrument
                    for instrument in eligible
                    if int(getattr(instrument, "expiry_unix_nanos")) == expiry
                    and str(getattr(instrument, "option_right", "") or "").upper()
                    == right
                ),
                key=lambda instrument: (
                    abs(Decimal(str(getattr(instrument, "strike"))) - spot),
                    Decimal(str(getattr(instrument, "strike"))),
                ),
            )
            selected.extend(candidates[:per_right])
        remaining_expiries -= 1
    return tuple(selected[:max_contracts])


def _price(value: object | None) -> str:
    return "-" if value is None else str(value)
