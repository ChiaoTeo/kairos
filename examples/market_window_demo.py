from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from kairospy.application.support.runtime.orchestration.kernel import RuntimeKernel
from kairospy.application.support.runtime.components import RuntimeComponents
from kairospy.application.usecases.market.subscriptions import DataSubscription, MarketDataSubscriptionSpec
from kairospy.application.support.runtime.events import RuntimeEnvelope
from kairospy.application.support.runtime.services.application import RuntimeApplicationServices, RuntimeServiceDependencies
from kairospy.application.support.runtime.services.market.modes.live import LiveMarketDataService
from kairospy.application.support.runtime.services.market.common import MarketSubscriptionState
from kairospy.application.support.system.facade.market import DriverName, ExchangeName, exchange
from kairospy.core.intent import IntentJournal
from kairospy.core.market import Bar, MarketEvent, MarketSelector, MarketSubject, OrderBookSnapshot, PriceLevel, Quote, RateObservation, TradePrint
from kairospy.core.reference import MarketRef, MarketResolver


SUPPORTED_REAL_SELECTORS = {"quote", "trade", "orderbook"}
SUPPORTED_SYNTHETIC_SELECTORS = {"quote", "trade", "orderbook", "bar", "rate"}


class MarketWindowDemoStrategy:
    strategy_id = "market-window-demo"

    def __init__(
        self,
        *,
        symbol: str,
        exchange_id: str,
        market_type: str,
        selectors: tuple[str, ...],
        orderbook_depth: int | str | None,
        orderbook_sync: str,
        orderbook_levels: int,
        orderbook_view: str,
        orderbook_render: str,
        max_events: int,
        output_format: str,
    ) -> None:
        self.symbol = symbol
        self.exchange_id = exchange_id
        self.market_type = market_type
        self.selectors = selectors
        self.orderbook_depth = orderbook_depth
        self.orderbook_sync = orderbook_sync
        self.orderbook_levels = orderbook_levels
        self.orderbook_view = orderbook_view
        self.orderbook_render = orderbook_render
        self.max_events = max_events
        self.output_format = output_format
        self.event_count = 0

    def on_start(self, context: object) -> None:
        for selector in _market_selectors(self.selectors, orderbook_depth=self.orderbook_depth, orderbook_sync=self.orderbook_sync):
            context.subscribe(  # type: ignore[attr-defined]
                self.symbol,
                exchange=self.exchange_id,
                market_type=self.market_type,
                selectors=(selector,),
                identity=f"{self.strategy_id}-{_selector_name(selector)}",
            )

    def on_data(self, context: object, signal: RuntimeEnvelope) -> None:
        self.event_count += 1
        market = context.market  # type: ignore[attr-defined]
        quotes = market.quotes(self.symbol, exchange=self.exchange_id, market_type=self.market_type)
        trades = market.trades(self.symbol, exchange=self.exchange_id, market_type=self.market_type)
        orderbooks = market.orderbooks(self.symbol, exchange=self.exchange_id, market_type=self.market_type)
        bars = market.bars(self.symbol, timeframe="1m", exchange=self.exchange_id, market_type=self.market_type)
        rates = market.rates(self.symbol, basis="funding_rate", exchange=self.exchange_id, market_type=self.market_type)
        if self.output_format == "line":
            print(_event_line(self.event_count, signal.kind, quotes, trades, orderbooks, bars, rates))
            return
        if signal.kind == "orderbook" and self.orderbook_view in {"ladder", "both"}:
            if self.orderbook_render == "screen":
                _clear_screen()
            print(_orderbook_ladder(self.event_count, orderbooks, levels=self.orderbook_levels))
            if self.orderbook_view == "ladder":
                return
        print(_event_table(self.event_count, signal.kind, quotes, trades, orderbooks, bars, rates))

    def on_intent(self, context: object, intent: object) -> None:
        return None

    def on_clock(self, context: object, signal: object) -> None:
        return None

    def on_system(self, context: object, signal: object) -> None:
        return None

    def on_end(self, context: object) -> None:
        print(f"market window demo finished after {self.event_count} market events")


class SyntheticRealtimeMarketData(MarketSubscriptionState):
    def __init__(self, *, market: MarketRef, ticks: int, interval_seconds: float = 0.1) -> None:
        super().__init__()
        self.market = market
        self.ticks = ticks
        self.interval_seconds = interval_seconds
        self._sequence = 0

    async def events(self) -> AsyncIterator[RuntimeEnvelope]:
        for tick in range(self.ticks):
            if tick:
                await asyncio.sleep(self.interval_seconds)
            now = datetime.now(timezone.utc)
            for subscription in self.subscriptions():
                for selector in subscription.spec.selectors:
                    event = self._event(selector, tick=tick, observed_at=now)
                    if event is not None:
                        self._sequence += 1
                        yield RuntimeEnvelope("market", event.kind, event.available_at or event.observed_at, self._sequence, event)

    def _event(self, selector: MarketSelector, *, tick: int, observed_at: datetime) -> MarketEvent | None:
        model = selector.model
        price = Decimal("100") + Decimal(tick)
        if model is Quote:
            value = Quote(
                instrument_id=self.market.instrument_id,
                market_id=self.market.market_id,
                market_key=self.market.market_key,
                time=observed_at,
                bid=price,
                ask=price + Decimal("0.5"),
                bid_size=Decimal("1") + Decimal(tick),
                ask_size=Decimal("1.5") + Decimal(tick),
                source="synthetic",
            )
            return _market_event(self.market, "quote", observed_at, value)
        if model is TradePrint:
            value = TradePrint(
                instrument_id=self.market.instrument_id,
                market_id=self.market.market_id,
                market_key=self.market.market_key,
                time=observed_at,
                trade_id=f"synthetic-{tick}",
                side="buy" if tick % 2 == 0 else "sell",
                price=price + Decimal("0.25"),
                size=Decimal("0.1") + Decimal(tick) / Decimal("10"),
                source="synthetic",
            )
            return _market_event(self.market, "trade", observed_at, value)
        if model is OrderBookSnapshot:
            value = OrderBookSnapshot(
                instrument_id=self.market.instrument_id,
                market_id=self.market.market_id,
                market_key=self.market.market_key,
                time=observed_at,
                bids=(PriceLevel(price, Decimal("2") + Decimal(tick)), PriceLevel(price - Decimal("1"), Decimal("4"))),
                asks=(PriceLevel(price + Decimal("0.5"), Decimal("3") + Decimal(tick)), PriceLevel(price + Decimal("1"), Decimal("5"))),
                nonce=tick,
                source="synthetic",
            )
            return _market_event(self.market, "orderbook", observed_at, value)
        if model is Bar:
            value = Bar(
                instrument_id=self.market.instrument_id,
                market_id=self.market.market_id,
                market_key=self.market.market_key,
                time=observed_at.replace(second=0, microsecond=0) + timedelta(minutes=tick),
                timeframe=selector.interval or "1m",
                open=price - Decimal("1"),
                high=price + Decimal("1"),
                low=price - Decimal("2"),
                close=price,
                volume=Decimal("10") + Decimal(tick),
                source="synthetic",
            )
            return _market_event(self.market, "bar", observed_at, value)
        if model is RateObservation:
            value = RateObservation(
                rate_id=str(self.market.market_id),
                market_id=self.market.market_id,
                instrument_id=self.market.instrument_id,
                time=observed_at,
                rate=Decimal("0.0001") + Decimal(tick) / Decimal("100000"),
                basis=selector.basis or "funding_rate",
                mark_price=price,
                source="synthetic",
            )
            return _market_event(self.market, "funding_rate", observed_at, value)
        return None


async def run_demo(args: argparse.Namespace) -> None:
    market = MarketResolver(default_venue=args.exchange, default_market=args.market_type).resolve(args.symbol)
    selectors = _parse_selectors(args.selectors, source=args.source)
    strategy = MarketWindowDemoStrategy(
        symbol=args.symbol,
        exchange_id=args.exchange,
        market_type=args.market_type,
        selectors=selectors,
        orderbook_depth=_parse_orderbook_depth(args.orderbook_depth),
        orderbook_sync=args.orderbook_sync,
        orderbook_levels=args.orderbook_levels,
        orderbook_view=args.orderbook_view,
        orderbook_render=args.orderbook_render,
        max_events=args.events,
        output_format=args.format,
    )
    if args.source == "real":
        connector = exchange(ExchangeName(args.exchange), DriverName.ccxt)
        data = LiveMarketDataService(feed=connector, source_name=f"{args.exchange}-live")
        data.set_stop_requested(lambda: strategy.event_count >= args.events)
    else:
        data = SyntheticRealtimeMarketData(market=market, ticks=args.ticks, interval_seconds=args.interval)
    intents = IntentJournal()
    kernel = RuntimeKernel(
        strategy,
        components=RuntimeComponents(market=data),
        services=RuntimeApplicationServices.from_dependencies(RuntimeServiceDependencies(intents=intents, data=data)),
    )
    await kernel.run()
    print("window index:")
    summaries = kernel.views.require("market.windows").windows
    if args.format == "line":
        for summary in summaries:
            print(f"  {summary.key} items={summary.item_count} events={summary.event_count} updated_at={summary.updated_at}")
    else:
        print(
            _table(
                ("kind", "key", "items", "events", "updated_at"),
                (
                    (summary.kind, summary.key, summary.item_count, summary.event_count, summary.updated_at)
                    for summary in summaries
                ),
            )
        )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Demo market window subscriptions and strategy reads.")
    parser.add_argument("--source", choices=("synthetic", "real"), default="synthetic")
    parser.add_argument("--exchange", default="binance", choices=("binance", "okx", "okex", "hyperliquid"))
    parser.add_argument("--market-type", default="spot")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--events", type=int, default=12, help="stop after this many real feed events")
    parser.add_argument("--ticks", type=int, default=3, help="synthetic ticks; each tick emits subscribed selector events")
    parser.add_argument("--interval", type=float, default=0.1, help="synthetic tick interval seconds")
    parser.add_argument("--format", choices=("table", "line"), default="table", help="output format")
    parser.add_argument("--orderbook-depth", default="5", help="orderbook depth: positive integer or full")
    parser.add_argument("--orderbook-sync", choices=("exchange", "local"), default="exchange", help="orderbook sync mode")
    parser.add_argument("--orderbook-levels", type=int, default=10, help="visible levels in orderbook ladder output")
    parser.add_argument("--orderbook-view", choices=("ladder", "window", "both"), default="ladder", help="table output for orderbook events")
    parser.add_argument("--orderbook-render", choices=("screen", "append"), default="screen", help="orderbook ladder render mode")
    parser.add_argument(
        "--selectors",
        default="all",
        help="comma-separated selectors: quote,trade,orderbook,bar,rate,all. Real mode supports quote,trade,orderbook.",
    )
    args = parser.parse_args(argv)
    asyncio.run(run_demo(args))


def _market_event(market: MarketRef, kind: str, observed_at: datetime, value: object) -> MarketEvent:
    _ = kind
    return MarketEvent(
        subject=MarketSubject("market", market.market_id),
        observed_at=observed_at,
        value=value,
        available_at=observed_at,
        source=getattr(value, "source", ""),
    )


def _selector_name(selector: MarketSelector | type) -> str:
    if isinstance(selector, MarketSelector):
        return selector.key.replace("|", "-")
    return selector.__name__


def _parse_selectors(value: str, *, source: str) -> tuple[str, ...]:
    supported = SUPPORTED_SYNTHETIC_SELECTORS if source == "synthetic" else SUPPORTED_REAL_SELECTORS
    raw = tuple(part.strip().lower() for part in value.split(",") if part.strip())
    selectors = tuple(sorted(supported)) if not raw or "all" in raw else raw
    unknown = tuple(selector for selector in selectors if selector not in supported)
    if unknown:
        raise ValueError(f"{source} demo does not support selector(s): {', '.join(unknown)}")
    return selectors


def _market_selectors(
    selectors: tuple[str, ...],
    *,
    orderbook_depth: int | str | None,
    orderbook_sync: str,
) -> tuple[MarketSelector | type, ...]:
    values: list[MarketSelector | type] = []
    for selector in selectors:
        if selector == "quote":
            values.append(Quote)
        elif selector == "trade":
            values.append(TradePrint)
        elif selector == "orderbook":
            derivation = "local_l2" if orderbook_sync == "local" else "direct"
            values.append(OrderBookSnapshot.select(depth=orderbook_depth, derivation=derivation))
        elif selector == "bar":
            values.append(Bar.select(interval="1m"))
        elif selector == "rate":
            values.append(RateObservation.select(basis="funding_rate"))
    return tuple(values)


def _parse_orderbook_depth(value: str) -> int | str:
    text = str(value).strip().lower()
    if text == "full":
        return "full"
    depth = int(text)
    if depth <= 0:
        raise ValueError("orderbook depth must be positive or full")
    return depth


def _event_line(
    event_count: int,
    event_kind: str,
    quotes: object,
    trades: object,
    orderbooks: object,
    bars: object,
    rates: object,
) -> str:
    return " | ".join(
        [
            f"#{event_count}",
            f"event={event_kind}",
            f"quote={_quote_text(quotes.latest)} qwin={quotes.size}",
            f"trade={_trade_text(trades.latest)} twin={trades.size}",
            f"book={_book_text(orderbooks.current)} bwin={orderbooks.size} spread_delta={None if orderbooks.change is None else orderbooks.change.spread_change}",
            f"bar={_bar_text(bars.latest)} barwin={bars.size}",
            f"rate={_rate_text(rates.latest)} ratewin={rates.size}",
        ]
    )


def _event_table(
    event_count: int,
    event_kind: str,
    quotes: object,
    trades: object,
    orderbooks: object,
    bars: object,
    rates: object,
) -> str:
    return _table(
        ("event", "window", "size", "latest", "previous/change"),
        (
            (f"#{event_count} {event_kind}", "quotes", quotes.size, _quote_text(quotes.latest), _quote_text(quotes.previous)),
            (f"#{event_count} {event_kind}", "trades", trades.size, _trade_text(trades.latest), _trade_text(trades.previous)),
            (
                f"#{event_count} {event_kind}",
                "orderbooks",
                orderbooks.size,
                _book_text(orderbooks.current),
                f"spread_delta={None if orderbooks.change is None else orderbooks.change.spread_change}",
            ),
            (f"#{event_count} {event_kind}", "bars.1m", bars.size, _bar_text(bars.latest), _bar_text(bars.previous)),
            (f"#{event_count} {event_kind}", "rates.funding_rate", rates.size, _rate_text(rates.latest), _rate_text(rates.previous)),
        ),
    )


def _orderbook_ladder(event_count: int, orderbooks: object, *, levels: int) -> str:
    current = orderbooks.current
    previous = orderbooks.previous
    if current is None:
        return _table(("event", "orderbook"), ((f"#{event_count}", "none"),))
    visible_levels = max(1, levels)
    rows: list[tuple[object, ...]] = []
    for level_index, level in reversed(tuple(enumerate(current.asks[:visible_levels], start=1))):
        rows.append(
            (
                f"#{event_count}",
                "ask",
                level_index,
                level.price,
                level.size,
                _level_size_delta(previous, side="ask", price=level.price, size=level.size),
            )
        )
    rows.append(
        (
            f"#{event_count}",
            "spread",
            "",
            _spread_text(current),
            f"window={orderbooks.size}",
            f"nonce={current.nonce}",
        )
    )
    for level_index, level in enumerate(current.bids[:visible_levels], start=1):
        rows.append(
            (
                f"#{event_count}",
                "bid",
                level_index,
                level.price,
                level.size,
                _level_size_delta(previous, side="bid", price=level.price, size=level.size),
            )
        )
    return _table(("event", "side", "level", "price/spread", "size", "delta"), tuple(rows))


def _clear_screen() -> None:
    if not sys.stdout.isatty():
        return
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _table(headers: Sequence[object], rows: Sequence[Sequence[object]]) -> str:
    try:
        from prettytable import PrettyTable
    except ImportError as error:
        raise RuntimeError("market window demo table output requires prettytable; install project dependencies first") from error
    table = PrettyTable()
    table.field_names = [str(header) for header in headers]
    table.align = "l"
    for row in rows:
        table.add_row(["" if value is None else str(value) for value in row])
    return table.get_string()


def _quote_text(value: Quote | None) -> str:
    return "none" if value is None else f"bid={value.bid} ask={value.ask}"


def _trade_text(value: TradePrint | None) -> str:
    return "none" if value is None else f"price={value.price} size={value.size}"


def _book_text(value: OrderBookSnapshot | None) -> str:
    if value is None:
        return "none"
    bid = None if value.bid1 is None else value.bid1.price
    ask = None if value.ask1 is None else value.ask1.price
    return f"bid1={bid} ask1={ask} bids={len(value.bids)} asks={len(value.asks)} nonce={value.nonce}"


def _spread_text(book: OrderBookSnapshot) -> str:
    if book.bid1 is None or book.ask1 is None:
        return "none"
    spread = book.ask1.price - book.bid1.price
    mid = (book.ask1.price + book.bid1.price) / Decimal("2")
    return f"spread={spread} mid={mid}"


def _level_size_delta(previous: OrderBookSnapshot | None, *, side: str, price: Decimal, size: Decimal) -> str:
    if previous is None:
        return ""
    levels = previous.asks if side == "ask" else previous.bids
    previous_size = next((level.size for level in levels if level.price == price), None)
    if previous_size is None:
        return "new"
    delta = size - previous_size
    if delta == 0:
        return ""
    return f"{delta:+}"


def _bar_text(value: Bar | None) -> str:
    return "none" if value is None else f"{value.timeframe} close={value.close}"


def _rate_text(value: RateObservation | None) -> str:
    return "none" if value is None else f"{value.basis}={value.rate}"


if __name__ == "__main__":
    main()
