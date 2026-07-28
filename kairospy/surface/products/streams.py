from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from enum import Enum
import sys
from typing import Mapping, TextIO

import typer

from kairospy.application.service.domains.market import MarketDataResolver, MarketDataService, MarketDataSpec
from kairospy.surface.runtime import DriverName, ExchangeName, StorageFormat, exchange, store
from kairospy.surface.ui.terminal import write_jsonl


class StreamKind(str, Enum):
    ticker = "ticker"
    orderbook = "orderbook"
    trades = "trades"


streams_app = typer.Typer(no_args_is_help=True, help="Market data stream commands")


@streams_app.command("print")
def print_stream(
    kind: StreamKind = typer.Option(..., "--kind"),
    symbol: str = typer.Option(..., "--symbol"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    limit: int | None = typer.Option(None, "--limit"),
    book_limit: int | None = typer.Option(None, "--book-limit"),
    trade_limit: int = typer.Option(50, "--trade-limit"),
    poll_seconds: float = typer.Option(1.0, "--poll-seconds"),
) -> None:
    events = stream_events(exchange_name, driver_name, kind, symbol, limit, book_limit, trade_limit, poll_seconds)
    asyncio.run(print_events(events, limit=limit, stdout=sys.stdout))


@streams_app.command("persist")
def persist(
    dataset: str | None = typer.Option(None, "--dataset"),
    kind: StreamKind = typer.Option(..., "--kind"),
    symbol: str = typer.Option(..., "--symbol"),
    root: str = typer.Option(".kairos/data", "--root"),
    storage_format: StorageFormat = typer.Option(StorageFormat.parquet, "--format"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str = typer.Option("spot", "--market"),
    limit: int | None = typer.Option(None, "--limit"),
    book_limit: int | None = typer.Option(None, "--book-limit"),
    trade_limit: int = typer.Option(50, "--trade-limit"),
    poll_seconds: float = typer.Option(1.0, "--poll-seconds"),
) -> None:
    service = MarketDataService(
        store(root, storage_format),
        MarketDataResolver(default_venue=exchange_name.value, default_market=market),
    )
    spec = MarketDataSpec(
        symbol=symbol,
        kind=kind.value,
        venue=exchange_name.value,
        market=market,
        dataset=dataset,
    )
    events = stream_events(exchange_name, driver_name, kind, symbol, limit, book_limit, trade_limit, poll_seconds)
    count = asyncio.run(service.persist(spec, events, limit=limit))
    typer.echo(str(count))


async def print_events(events: AsyncIterable[Mapping[str, object]], *, limit: int | None, stdout: TextIO) -> int:
    count = 0
    async for event in events:
        write_jsonl((event,), stdout)
        count += 1
        if limit is not None and count >= limit:
            break
    return 0


def stream_events(
    exchange_name: ExchangeName,
    driver_name: DriverName,
    kind: StreamKind,
    symbol: str,
    limit: int | None,
    book_limit: int | None,
    trade_limit: int,
    poll_seconds: float,
) -> AsyncIterable[Mapping[str, object]]:
    exchange_client = exchange(exchange_name, driver_name)
    params: dict[str, object] = {"poll_seconds": poll_seconds}
    if limit is not None:
        params["max_events"] = limit
    if kind is StreamKind.ticker:
        return exchange_client.watch_ticker(symbol, params=params)
    if kind is StreamKind.orderbook:
        return exchange_client.watch_order_book(symbol, limit=book_limit, params=params)
    if kind is StreamKind.trades:
        return exchange_client.watch_trades(symbol, limit=trade_limit, params=params)
    raise typer.BadParameter(f"unsupported market data stream kind: {kind.value}")


__all__ = [
    "StreamKind",
    "print_events",
    "stream_events",
    "streams_app",
]
