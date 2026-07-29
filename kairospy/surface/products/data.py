from __future__ import annotations

from enum import Enum
import json
import sys
from typing import Mapping

import typer

from kairospy.application.system.workspace import KairosWorkspace
from kairospy.application.service.domain.market import MarketDataResolver, MarketDataSpec, replay_rows
from kairospy.application.service.modes.backtest import BacktestMarketDataService
from kairospy.surface.runtime import DriverName, ExchangeName, StorageFormat, exchange, store
from kairospy.surface.ui.terminal import write_jsonl


class HistoricalKind(str, Enum):
    ohlcv = "ohlcv"


class WriteMode(str, Enum):
    append = "append"
    replace = "replace"


class OutputFormat(str, Enum):
    json = "json"
    text = "text"


data_app = typer.Typer(no_args_is_help=True, help="Historical data commands")


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
    exchange_client = exchange(exchange_name, driver_name)
    if kind is not HistoricalKind.ohlcv:
        raise typer.BadParameter(f"unsupported historical data kind: {kind.value}")
    spec = MarketDataSpec(
        symbol=symbol,
        kind=kind.value,
        venue=exchange_name.value,
        market=market,
        timeframe=timeframe,
        start=start,
        end=end,
        limit=limit,
        dataset=dataset,
    )
    path = _service(root, storage_format, exchange_name=exchange_name, market=market).download(spec, exchange_client, mode=mode.value)
    typer.echo(str(path))


@data_app.command("list")
def list_datasets(
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    output_format: OutputFormat = typer.Option(OutputFormat.text, "--output"),
) -> None:
    data_store = store(root, storage_format)
    datasets = [str(item) for item in data_store.list()]
    aliases = data_store.aliases()
    payload = {"root": str(data_store.root), "datasets": datasets, "aliases": aliases, "count": len(datasets)}
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    if not datasets:
        typer.echo(f"Datasets\n  none\n  root {data_store.root}")
        return
    lines = ["Datasets"]
    lines.extend(f"  {item}" for item in datasets)
    if aliases:
        lines.append("Aliases")
        lines.extend(f"  {name} -> {target}" for name, target in aliases.items())
    typer.echo("\n".join(lines))


@data_app.command("inspect")
def inspect_dataset(
    dataset: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    sample: int = typer.Option(3, "--sample"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--output"),
) -> None:
    data_store = store(root, storage_format)
    rows = data_store.read_rows(dataset)
    times = [str(row.get("time")) for row in rows if row.get("time") is not None]
    data_path = data_store._existing_data_path(dataset)
    payload = {
        "dataset": str(data_store.resolve(dataset)),
        "path": str(data_path) if data_path is not None else None,
        "rows": len(rows),
        "start": min(times) if times else None,
        "end": max(times) if times else None,
        "columns": sorted({key for row in rows for key in row}),
        "sample": rows[:sample],
    }
    if output_format is OutputFormat.json:
        _echo(payload)
        return
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


@data_app.command("alias")
def alias_dataset(
    dataset: str = typer.Argument(...),
    alias: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
) -> None:
    data_store = store(root, storage_format)
    path = data_store.alias(dataset, alias)
    workspace = KairosWorkspace.resolve()
    workspace.operations.append("data.alias", target={"dataset": str(data_store.resolve(dataset)), "alias": alias}, payload={"path": path})
    _echo({"dataset": str(data_store.resolve(dataset)), "alias": alias, "path": str(path)})


@data_app.command("prune")
def prune(
    dataset: str = typer.Argument(...),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    output_format: OutputFormat = typer.Option(OutputFormat.json, "--output"),
) -> None:
    data_store = store(root, storage_format)
    result = data_store.delete_window(dataset, start=start, end=end)
    workspace = KairosWorkspace.resolve()
    workspace.operations.append("data.prune", target={"dataset": str(result["dataset"])}, payload=result)
    if output_format is OutputFormat.json:
        _echo(result)
        return
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


@data_app.command("read")
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
    if dataset is not None:
        rows = store(root, storage_format).read_rows(
            dataset,
            start=start,
            end=end,
            columns=columns,
            limit=limit,
        )
    else:
        if symbol is None:
            raise typer.BadParameter("dataset or --symbol is required")
        rows = _service(root, storage_format, exchange_name=exchange_name, market=market).read(
            MarketDataSpec(
                symbol=symbol,
                kind=kind.value,
                venue=exchange_name.value,
                market=market,
                timeframe=timeframe,
                start=start,
                end=end,
                limit=limit,
            )
        )
        if columns is not None:
            selected = set(columns)
            rows = [{key: value for key, value in row.items() if key in selected} for row in rows]
    write_jsonl(rows, sys.stdout)


@data_app.command("replay")
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
    if dataset is not None:
        rows = store(root, storage_format).read_rows(dataset, start=start, end=end, limit=limit)
    else:
        if symbol is None:
            raise typer.BadParameter("dataset or --symbol is required")
        rows = _service(root, storage_format, exchange_name=exchange_name, market=market).read(
            MarketDataSpec(
                symbol=symbol,
                kind=kind,
                venue=exchange_name.value,
                market=market,
                timeframe=timeframe,
                start=start,
                end=end,
                limit=limit,
            )
        )
    replay_rows(rows, speed=speed, write=lambda batch: write_jsonl(batch, sys.stdout))


def _service(
    root: str | None,
    storage_format: StorageFormat | None,
    *,
    exchange_name: ExchangeName,
    market: str,
) -> MarketDataPort:
    return BacktestMarketDataService(
        store(root, storage_format),
        resolver=MarketDataResolver(default_venue=exchange_name.value, default_market=market),
    )


def _echo(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))
