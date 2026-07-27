from __future__ import annotations

from enum import Enum
import sys
from time import monotonic, sleep
from typing import Iterable, Mapping, TextIO

import typer

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
    dataset: str = typer.Option(..., "--dataset"),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    exchange_name: ExchangeName = typer.Option(ExchangeName.binance, "--exchange"),
    driver_name: DriverName = typer.Option(DriverName.ccxt, "--driver"),
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
    rows = exchange_client.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        since=start,
        until=end,
        limit=limit,
    )
    path = store(root, storage_format).write(dataset, rows, mode=mode.value)
    typer.echo(str(path))


@data_app.command("read")
def read(
    dataset: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    columns: list[str] | None = typer.Option(None, "--columns"),
    limit: int | None = typer.Option(None, "--limit"),
) -> None:
    rows = store(root, storage_format).read_rows(
        dataset,
        start=start,
        end=end,
        columns=columns,
        limit=limit,
    )
    write_jsonl(rows, sys.stdout)


@data_app.command("replay")
def replay(
    dataset: str = typer.Argument(...),
    root: str | None = typer.Option(None, "--root"),
    storage_format: StorageFormat | None = typer.Option(None, "--format"),
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    limit: int | None = typer.Option(None, "--limit"),
    speed: float = typer.Option(1.0, "--speed"),
) -> None:
    rows = store(root, storage_format).read_rows(dataset, start=start, end=end, limit=limit)
    replay_rows(rows, speed=speed, stdout=sys.stdout)


def replay_rows(rows: Iterable[Mapping[str, object]], *, speed: float, stdout: TextIO) -> int:
    if speed < 0:
        raise ValueError("replay speed cannot be negative")
    previous_time: float | None = None
    wall_start = monotonic()
    replay_start: float | None = None
    for row in rows:
        current_time = _timestamp(row["time"])
        if speed > 0:
            if replay_start is None:
                replay_start = current_time
            target_elapsed = (current_time - replay_start) / speed
            sleep_seconds = target_elapsed - (monotonic() - wall_start)
            if previous_time is not None and sleep_seconds > 0:
                sleep(sleep_seconds)
        previous_time = current_time
        write_jsonl((row,), stdout)
    return 0


def _timestamp(value: object) -> float:
    from datetime import datetime, timezone

    if not isinstance(value, str):
        raise ValueError(f"replay row time must be ISO-8601 text: {value!r}")
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"replay row time must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc).timestamp()
