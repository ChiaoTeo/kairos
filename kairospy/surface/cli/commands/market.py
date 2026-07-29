from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from enum import Enum
import sys
from typing import Mapping, TextIO

import typer

from kairospy.application.system.facade.market import MarketDataFacade
from kairospy.application.system.facade.resources import DriverName, ExchangeName, StorageFormat
from kairospy.surface.cli.options import OutputFormat, resolve_output
from kairospy.surface.rendering.terminal import write_jsonl
from kairospy.surface.rendering.writer import write_result


class HistoricalKind(str, Enum):
    ohlcv = "ohlcv"


class WriteMode(str, Enum):
    append = "append"
    replace = "replace"


market_app = typer.Typer(no_args_is_help=True, help="Market data commands")
_MARKET_DATA = MarketDataFacade()


class StreamKind(str, Enum):
    ticker = "ticker"
    orderbook = "orderbook"
    trades = "trades"


@market_app.command("download")
def download(
    symbol: str = typer.Option(..., "--symbol"),
    dataset: str | None = typer.Option(None, "--dataset"),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str = typer.Option("spot", "--market"),
    kind: HistoricalKind = typer.Option(HistoricalKind.ohlcv, "--kind"),
    timeframe: str = typer.Option("1m", "--timeframe"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    limit: int = typer.Option(1000, "--limit"),
    mode: WriteMode = typer.Option(WriteMode.append, "--mode"),
) -> None:
    if kind is not HistoricalKind.ohlcv:
        raise typer.BadParameter(f"unsupported historical data kind: {kind.value}")
    try:
        path = _MARKET_DATA.download(
            symbol=symbol,
            dataset=dataset,
            root=root,
            storage_format=storage_format,
            exchange_name=exchange_name,
            driver_name=driver_name,
            market=market,
            kind=kind.value,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            mode=mode.value,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(path)


@market_app.command("list")
def list_datasets(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    payload = _MARKET_DATA.list_datasets(root=root, storage_format=storage_format)
    write_result(payload, output=resolve_output(ctx, output_format), text=_render_datasets)


@market_app.command("inspect")
def inspect_dataset(
    ctx: typer.Context,
    dataset: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    sample: int = typer.Option(3, "--sample"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    payload = _MARKET_DATA.inspect_dataset(dataset=dataset, root=root, storage_format=storage_format, sample=sample)
    write_result(payload, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@market_app.command("alias")
def alias_dataset(
    dataset: str = typer.Argument(...),
    alias: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
) -> None:
    _echo(_MARKET_DATA.alias_dataset(dataset=dataset, alias=alias, root=root, storage_format=storage_format))


@market_app.command("prune")
def prune(
    ctx: typer.Context,
    dataset: str = typer.Argument(...),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    result = _MARKET_DATA.prune(dataset=dataset, start=start, end=end, root=root, storage_format=storage_format)
    write_result(result, output=resolve_output(ctx, output_format, default=OutputFormat.json))


@market_app.command("read")
def read(
    dataset: str | None = typer.Argument(None),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    market: str = typer.Option("spot", "--market"),
    kind: HistoricalKind = typer.Option(HistoricalKind.ohlcv, "--kind"),
    timeframe: str = typer.Option("1m", "--timeframe"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    columns: list[str] | None = typer.Option(None, "--columns"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    try:
        rows = _MARKET_DATA.read(
            dataset=dataset,
            root=root,
            storage_format=storage_format,
            symbol=symbol,
            exchange_name=exchange_name,
            market=market,
            kind=kind.value,
            timeframe=timeframe,
            start=start,
            end=end,
            columns=columns,
            limit=limit,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_jsonl(rows, sys.stdout)


@market_app.command("replay")
def replay(
    dataset: str | None = typer.Argument(None),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    market: str = typer.Option("spot", "--market"),
    kind: str = typer.Option("trades", "--kind"),
    timeframe: str | None = typer.Option(None, "--timeframe"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    limit: int | None = typer.Option(None, "--limit"),
    speed: float = typer.Option(1.0, "--speed"),
) -> None:
    try:
        _MARKET_DATA.replay(
            dataset=dataset,
            root=root,
            storage_format=storage_format,
            symbol=symbol,
            exchange_name=exchange_name,
            market=market,
            kind=kind,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            speed=speed,
            write=lambda batch: write_jsonl(batch, sys.stdout),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@market_app.command("watch")
def watch(
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


@market_app.command("persist")
def persist(
    dataset: str | None = typer.Option(None, "--dataset"),
    kind: StreamKind = typer.Option(..., "--kind"),
    symbol: str = typer.Option(..., "--symbol"),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    market: str = typer.Option("spot", "--market"),
    limit: int | None = typer.Option(None, "--limit"),
    book_limit: int | None = typer.Option(None, "--book-limit"),
    trade_limit: int = typer.Option(50, "--trade-limit"),
    poll_seconds: float = typer.Option(1.0, "--poll-seconds"),
) -> None:
    try:
        count = asyncio.run(
            _MARKET_DATA.persist(
                dataset=dataset,
                kind=kind.value,
                symbol=symbol,
                root=root,
                storage_format=storage_format,
                exchange_name=exchange_name,
                driver_name=driver_name,
                market=market,
                limit=limit,
                book_limit=book_limit,
                trade_limit=trade_limit,
                poll_seconds=poll_seconds,
            )
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(str(count))


@market_app.command("doctor")
def doctor(
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
) -> None:
    try:
        payload = _MARKET_DATA.doctor(exchange_name=exchange_name, driver_name=driver_name)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    _echo(payload)


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
    try:
        return _MARKET_DATA.stream_events(
            exchange_name=exchange_name,
            driver_name=driver_name,
            kind=kind.value,
            symbol=symbol,
            limit=limit,
            book_limit=book_limit,
            trade_limit=trade_limit,
            poll_seconds=poll_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


def _echo(payload: Mapping[str, object]) -> None:
    write_result(payload, output=OutputFormat.json)


def _render_datasets(result: object) -> str:
    if not isinstance(result, Mapping):
        raise TypeError("market renderer expected mapping payload")
    datasets = result["datasets"]
    aliases = result["aliases"]
    if not datasets:
        return f"Datasets\n  none\n  root {result['root']}"
    lines = ["Datasets"]
    if isinstance(datasets, list):
        lines.extend(f"  {item}" for item in datasets)
    if isinstance(aliases, Mapping) and aliases:
        lines.append("Aliases")
        lines.extend(f"  {name} -> {target}" for name, target in aliases.items())
    return "\n".join(lines)


__all__ = [
    "HistoricalKind",
    "StreamKind",
    "WriteMode",
    "market_app",
    "print_events",
    "stream_events",
]
