from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from enum import Enum
import sys
from typing import Mapping, TextIO

import typer

from kairospy.application.support.system.application.facade.market import DriverName, ExchangeName, MarketDataFacade, StorageFormat
from kairospy.surface.cli.options import OutputFormat
from kairospy.surface.cli.output import write_cli_result
from kairospy.surface.cli.options import resolve_output
from kairospy.surface.rendering.terminal import write_jsonl
from kairospy.surface.rendering.writer import render_text


class HistoricalKind(str, Enum):
    ohlcv = "ohlcv"


class WriteMode(str, Enum):
    append = "append"
    replace = "replace"


market_app = typer.Typer(no_args_is_help=True, help="Market data commands")
source_app = typer.Typer(no_args_is_help=True, help="Market data source commands")
data_app = typer.Typer(no_args_is_help=True, help="Historical market data commands")
dataset_app = typer.Typer(no_args_is_help=True, help="Local market dataset commands")
stream_app = typer.Typer(no_args_is_help=True, help="Live market stream commands")
market_app.add_typer(source_app, name="source")
market_app.add_typer(data_app, name="data")
market_app.add_typer(dataset_app, name="dataset")
market_app.add_typer(stream_app, name="stream")
_MARKET_DATA = MarketDataFacade()


class StreamKind(str, Enum):
    ticker = "ticker"
    orderbook = "orderbook"
    trades = "trades"


class DataMode(str, Enum):
    historical = "historical"
    live = "live"


@source_app.command("capabilities")
def capabilities(
    ctx: typer.Context,
    exchange_name: ExchangeName | None = typer.Option(None, "--exchange"),
    market: str | None = typer.Option(None, "--market"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    payload = _MARKET_DATA.capabilities(exchange_name=exchange_name, market=market, driver_name=driver_name)
    write_cli_result(ctx, payload, output_format=output_format, text=_render_capabilities)


@source_app.command("check")
def check(
    ctx: typer.Context,
    symbol: str = typer.Option(..., "--symbol"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    market: str = typer.Option("spot", "--market"),
    kind: str = typer.Option("bar", "--kind"),
    data_mode: DataMode = typer.Option(DataMode.historical, "--mode"),
    timeframe: str | None = typer.Option(None, "--timeframe"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    payload = _MARKET_DATA.check(
        symbol=symbol,
        exchange_name=exchange_name,
        market=market,
        kind=kind,
        data_mode=data_mode.value,  # type: ignore[arg-type]
        timeframe=timeframe,
        driver_name=driver_name,
    )
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_check)


@data_app.command("download")
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


@data_app.command("prefetch")
def prefetch(
    ctx: typer.Context,
    config_path: str = typer.Argument(...),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    limit: int = typer.Option(1000, "--limit"),
    mode: WriteMode = typer.Option(WriteMode.append, "--mode"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    try:
        payload = _MARKET_DATA.prefetch_backtest(
            config_path=config_path,
            driver_name=driver_name,
            limit=limit,
            mode=mode.value,
            dry_run=dry_run,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_prefetch)


@dataset_app.command("list")
def list_datasets(
    ctx: typer.Context,
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    payload = _MARKET_DATA.list_datasets(root=root, storage_format=storage_format)
    write_cli_result(ctx, payload, output_format=output_format, text=_render_datasets)


@dataset_app.command("inspect")
def inspect_dataset(
    ctx: typer.Context,
    dataset: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    sample: int = typer.Option(3, "--sample"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    payload = _MARKET_DATA.inspect_dataset(dataset=dataset, root=root, storage_format=storage_format, sample=sample)
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json)


@dataset_app.command("alias")
def alias_dataset(
    ctx: typer.Context,
    dataset: str = typer.Argument(...),
    alias: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    payload = _MARKET_DATA.alias_dataset(dataset=dataset, alias=alias, root=root, storage_format=storage_format)
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_alias)


@dataset_app.command("prune")
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
    write_cli_result(ctx, result, output_format=output_format, default=OutputFormat.json)


@dataset_app.command("read")
def read(
    ctx: typer.Context,
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
    output_format: OutputFormat | None = typer.Option(None, "--output"),
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
    write_cli_result(ctx, rows, output_format=output_format, default=OutputFormat.jsonl, text=render_text)


@stream_app.command("replay")
def replay(
    ctx: typer.Context,
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
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    output = resolve_output(ctx, output_format, default=OutputFormat.jsonl)
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
            write=lambda batch: _write_stream_batch(batch, output=output),
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


@stream_app.command("watch")
def watch(
    ctx: typer.Context,
    dataset: str | None = typer.Argument(None),
    kind: StreamKind | None = typer.Option(None, "--kind"),
    symbol: str | None = typer.Option(None, "--symbol"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    limit: int | None = typer.Option(None, "--limit"),
    book_limit: int | None = typer.Option(None, "--book-limit"),
    trade_limit: int = typer.Option(50, "--trade-limit"),
    poll_seconds: float = typer.Option(1.0, "--poll-seconds"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    output = resolve_output(ctx, output_format, default=OutputFormat.jsonl)
    events = stream_events(dataset, exchange_name, driver_name, kind, symbol, limit, book_limit, trade_limit, poll_seconds)
    asyncio.run(print_events(events, limit=limit, stdout=sys.stdout, output=output))


@stream_app.command("persist")
def persist(
    dataset: str | None = typer.Argument(None),
    dataset_option: str | None = typer.Option(None, "--dataset"),
    kind: StreamKind | None = typer.Option(None, "--kind"),
    symbol: str | None = typer.Option(None, "--symbol"),
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
        resolved_dataset = dataset or dataset_option
        count = asyncio.run(
            _MARKET_DATA.persist(
                dataset=resolved_dataset,
                kind=None if kind is None else kind.value,
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


@source_app.command("doctor")
def doctor(
    ctx: typer.Context,
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
    output_format: OutputFormat | None = typer.Option(None, "--output"),
) -> None:
    try:
        payload = _MARKET_DATA.doctor(exchange_name=exchange_name, driver_name=driver_name)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    write_cli_result(ctx, payload, output_format=output_format, default=OutputFormat.json, text=_render_doctor)


async def print_events(
    events: AsyncIterable[Mapping[str, object]],
    *,
    limit: int | None,
    stdout: TextIO,
    output: OutputFormat,
) -> int:
    count = 0
    async for event in events:
        _write_stream_batch((event,), output=output, stdout=stdout)
        count += 1
        if limit is not None and count >= limit:
            break
    return 0


def _write_stream_batch(
    batch: object,
    *,
    output: OutputFormat,
    stdout: TextIO | None = None,
) -> None:
    stream = stdout or sys.stdout
    if output is OutputFormat.text:
        stream.write(render_text(batch) + "\n")
        stream.flush()
        return
    write_jsonl(batch if isinstance(batch, (tuple, list)) else (batch,), stream)  # type: ignore[arg-type]


def stream_events(
    dataset: str | None,
    exchange_name: ExchangeName,
    driver_name: DriverName,
    kind: StreamKind | None,
    symbol: str | None,
    limit: int | None,
    book_limit: int | None,
    trade_limit: int,
    poll_seconds: float,
) -> AsyncIterable[Mapping[str, object]]:
    try:
        return _MARKET_DATA.stream_events(
            dataset=dataset,
            exchange_name=exchange_name,
            driver_name=driver_name,
            kind=None if kind is None else kind.value,
            symbol=symbol,
            limit=limit,
            book_limit=book_limit,
            trade_limit=trade_limit,
            poll_seconds=poll_seconds,
        )
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


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


def _render_capabilities(result: object) -> str:
    if not isinstance(result, Mapping):
        raise TypeError("market renderer expected mapping payload")
    lines = [f"Market data capabilities ({result['driver']})"]
    for item in result.get("markets", ()):
        if not isinstance(item, Mapping):
            continue
        header = f"  {item['venue']} {item['market']}"
        if item.get("status") != "configured":
            lines.append(f"{header}: not configured")
            if item.get("reason"):
                lines.append(f"    reason: {item['reason']}")
            continue
        lines.append(header)
        historical = ", ".join(str(value.get("label") or value.get("kind")) for value in item.get("historical", ()) if isinstance(value, Mapping))
        live = ", ".join(str(value.get("label") or value.get("kind")) for value in item.get("live", ()) if isinstance(value, Mapping))
        lines.append(f"    historical: {historical or 'none'}")
        lines.append(f"    live: {live or 'none'}")
    return "\n".join(lines)


def _render_alias(result: object) -> str:
    if not isinstance(result, Mapping):
        raise TypeError("market renderer expected mapping payload")
    return "\n".join([
        "Dataset Alias",
        f"  alias    {result['alias']}",
        f"  dataset  {result['dataset']}",
        f"  path     {result['path']}",
    ])


def _render_doctor(result: object) -> str:
    if not isinstance(result, Mapping):
        raise TypeError("market renderer expected mapping payload")
    return "\n".join([
        "Market Doctor",
        f"  valid     {str(result['valid']).lower()}",
        f"  exchange  {result['exchange']}",
        f"  driver    {result['driver']}",
    ])


def _render_check(result: object) -> str:
    if not isinstance(result, Mapping):
        raise TypeError("market renderer expected mapping payload")
    lines = [
        f"Market data check: {'valid' if result.get('valid') else 'invalid'}",
        f"  market: {result['venue']} {result['market']} {result['symbol']}",
        f"  mode: {result['mode']}",
        f"  kind: {result['kind']}",
    ]
    if result.get("timeframe") is not None:
        lines.append(f"  timeframe: {result['timeframe']}")
    if result.get("dataset") is not None:
        lines.append(f"  dataset: {result['dataset']}")
    if result.get("reason") is not None:
        lines.append(f"  reason: {result['reason']}")
    return "\n".join(lines)


def _render_prefetch(result: object) -> str:
    if not isinstance(result, Mapping):
        raise TypeError("market renderer expected mapping payload")
    lines = [f"Market prefetch {'plan' if result.get('dry_run') else 'result'}: {result['launch_id']}"]
    for item in result.get("plan", ()):
        if not isinstance(item, Mapping):
            continue
        lines.append(f"  {item['venue']} {item['market']} {item['symbol']} {item['kind']} {item.get('timeframe') or ''}".rstrip())
        lines.append(f"    dataset: {item['dataset']}")
        lines.append(f"    window: {item['start']} -> {item['end']}")
        lines.append(f"    supported: {'yes' if item.get('supported') else 'no'}")
        if item.get("path") is not None:
            lines.append(f"    path: {item['path']}")
    return "\n".join(lines)


__all__ = [
    "HistoricalKind",
    "DataMode",
    "StreamKind",
    "WriteMode",
    "market_app",
    "print_events",
    "stream_events",
]
