from __future__ import annotations

from enum import Enum
import sys

import typer

from kairospy.application.service.domain.market import MarketDataResolver, MarketDataSpec, replay_rows
from kairospy.application.service.engine.backtest import BacktestMarketDataService
from kairospy.surface.runtime import DriverName, ExchangeName, StorageFormat, exchange, store
from kairospy.surface.ui.terminal import write_jsonl


class HistoricalKind(str, Enum):
    ohlcv = "ohlcv"


class WriteMode(str, Enum):
    append = "append"
    replace = "replace"


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
) -> MarketDataService:
    return BacktestMarketDataService(
        store(root, storage_format),
        resolver=MarketDataResolver(default_venue=exchange_name.value, default_market=market),
    )
