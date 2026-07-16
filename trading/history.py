from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Protocol

from trading.adapters.binance.adapter import BinanceTransport, RateLimiter, UrllibBinanceTransport
from trading.domain.identity import InstrumentId
from trading.domain.market_data import Bar


@dataclass(frozen=True, slots=True)
class BarMetadata:
    schema_version: int
    dataset_id: str
    instrument_id: InstrumentId
    symbol: str
    interval: str
    source: str
    start: datetime
    end: datetime
    bar_count: int


@dataclass(frozen=True, slots=True)
class BarDataset:
    metadata: BarMetadata
    bars: tuple[Bar, ...]

    def __post_init__(self) -> None:
        if not self.bars:
            raise ValueError("bar dataset cannot be empty")
        if tuple(sorted(self.bars, key=lambda bar: bar.start)) != self.bars:
            raise ValueError("bars must be ordered by start time")
        if len({bar.start for bar in self.bars}) != len(self.bars):
            raise ValueError("bar start times must be unique")
        if any(bar.instrument_id != self.metadata.instrument_id for bar in self.bars):
            raise ValueError("bar instrument does not match metadata")
        if self.metadata.bar_count != len(self.bars):
            raise ValueError("bar count does not match metadata")

    def frame(self):
        """Return a notebook-friendly pandas DataFrame indexed by bar start time."""
        try:
            import pandas as pd
        except ImportError as error:
            raise ImportError("pandas is required; install trader[notebook]") from error
        frame = pd.DataFrame(
            ({
                "time": bar.start,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            } for bar in self.bars)
        )
        return frame.set_index("time")

    def plot(
        self,
        *,
        indicators: Mapping[str, Any] | None = None,
        volume: bool = True,
        title: str | None = None,
        style: str = "yahoo",
        **kwargs: Any,
    ):
        """Plot candles and optional indicator Series; returns mplfinance axes."""
        try:
            import mplfinance as mpf
        except ImportError as error:
            raise ImportError("mplfinance is required; install trader[notebook]") from error
        frame = self.frame()
        additions = [mpf.make_addplot(values, label=name) for name, values in (indicators or {}).items()]
        return mpf.plot(
            frame,
            type="candle",
            volume=volume,
            title=title or f"{self.metadata.symbol} {self.metadata.interval}",
            style=style,
            addplot=additions or None,
            returnfig=True,
            **kwargs,
        )


class HistoricalBarProvider(Protocol):
    def fetch(self, instrument_id: InstrumentId, symbol: str, interval: str, start: datetime, end: datetime) -> tuple[Bar, ...]: ...


class BinanceHistoricalBarProvider:
    _ROUTES = {
        "spot": ("https://data-api.binance.vision", "/api/v3/klines"),
        "usdm": ("https://fapi.binance.com", "/fapi/v1/klines"),
        "coinm": ("https://dapi.binance.com", "/dapi/v1/klines"),
    }

    def __init__(self, market: str = "spot", transport: BinanceTransport | None = None, limiter: RateLimiter | None = None) -> None:
        if market not in self._ROUTES:
            raise ValueError(f"unsupported Binance market: {market}")
        self.market = market
        base_url, self.path = self._ROUTES[market]
        self.transport = transport or UrllibBinanceTransport(base_url)
        self.limiter = limiter or RateLimiter(1200, 60)

    def fetch(self, instrument_id: InstrumentId, symbol: str, interval: str, start: datetime, end: datetime) -> tuple[Bar, ...]:
        _validate_range(start, end)
        cursor, end_ms = _milliseconds(start), _milliseconds(end)
        result: list[Bar] = []
        while cursor < end_ms:
            self.limiter.acquire()
            rows = self.transport.request("GET", self.path, {
                "symbol": symbol.upper(), "interval": interval, "startTime": cursor,
                "endTime": end_ms - 1, "limit": 1000,
            })
            if not rows:
                break
            for row in rows:
                open_ms, close_ms = int(row[0]), int(row[6])
                if open_ms >= end_ms:
                    break
                result.append(Bar(
                    instrument_id,
                    datetime.fromtimestamp(open_ms / 1000, timezone.utc),
                    datetime.fromtimestamp((close_ms + 1) / 1000, timezone.utc),
                    Decimal(str(row[1])), Decimal(str(row[2])), Decimal(str(row[3])),
                    Decimal(str(row[4])), Decimal(str(row[5])),
                ))
            next_cursor = int(rows[-1][6]) + 1
            if next_cursor <= cursor:
                raise RuntimeError("Binance kline pagination did not advance")
            cursor = next_cursor
            if len(rows) < 1000:
                break
        return tuple(result)


class BarRepository:
    def __init__(self, root: str | Path = "data/history") -> None:
        self.root = Path(root)

    def save(self, dataset: BarDataset) -> Path:
        directory = self.root / dataset.metadata.dataset_id
        directory.mkdir(parents=True, exist_ok=True)
        metadata = {
            "schema_version": dataset.metadata.schema_version,
            "dataset_id": dataset.metadata.dataset_id,
            "instrument_id": str(dataset.metadata.instrument_id),
            "symbol": dataset.metadata.symbol,
            "interval": dataset.metadata.interval,
            "source": dataset.metadata.source,
            "start": dataset.metadata.start.isoformat(),
            "end": dataset.metadata.end.isoformat(),
            "bar_count": dataset.metadata.bar_count,
        }
        _atomic_text(directory / "metadata.json", json.dumps(metadata, indent=2, sort_keys=True) + "\n")
        temporary = directory / "bars.csv.tmp"
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("start", "end", "open", "high", "low", "close", "volume"))
            for bar in dataset.bars:
                writer.writerow((bar.start.isoformat(), bar.end.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume))
        temporary.replace(directory / "bars.csv")
        return directory

    def datasets(self) -> tuple[str, ...]:
        """Return saved dataset ids that contain both metadata and bar data."""
        if not self.root.exists():
            return ()
        return tuple(sorted(
            path.name for path in self.root.iterdir()
            if path.is_dir() and (path / "metadata.json").is_file() and (path / "bars.csv").is_file()
        ))

    def load(self, dataset_id: str) -> BarDataset:
        directory = self.root / dataset_id
        if not (directory / "metadata.json").is_file():
            available = ", ".join(self.datasets()) or "none"
            raise FileNotFoundError(
                f"historical dataset {dataset_id!r} was not found under {self.root.resolve()}; "
                f"available datasets: {available}. Download it first with 'trader history download'."
            )
        raw = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        if raw.get("schema_version") != 1:
            raise ValueError(f"unsupported bar dataset schema: {raw.get('schema_version')}")
        instrument_id = InstrumentId(raw["instrument_id"])
        metadata = BarMetadata(
            raw["schema_version"], raw["dataset_id"], instrument_id, raw["symbol"], raw["interval"],
            raw["source"], datetime.fromisoformat(raw["start"]), datetime.fromisoformat(raw["end"]), raw["bar_count"],
        )
        with (directory / "bars.csv").open(newline="", encoding="utf-8") as handle:
            bars = tuple(Bar(
                instrument_id, datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"]),
                Decimal(row["open"]), Decimal(row["high"]), Decimal(row["low"]),
                Decimal(row["close"]), Decimal(row["volume"]),
            ) for row in csv.DictReader(handle))
        return BarDataset(metadata, bars)

    def download(
        self, provider: HistoricalBarProvider, *, dataset_id: str, instrument_id: InstrumentId,
        symbol: str, interval: str, start: datetime, end: datetime, source: str,
    ) -> BarDataset:
        bars = provider.fetch(instrument_id, symbol, interval, start, end)
        if not bars:
            raise RuntimeError("historical data provider returned no bars")
        dataset = BarDataset(BarMetadata(1, dataset_id, instrument_id, symbol, interval, source, start, end, len(bars)), bars)
        self.save(dataset)
        return dataset


def _milliseconds(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("historical data timestamps must be timezone-aware")
    return int(value.timestamp() * 1000)


def _validate_range(start: datetime, end: datetime) -> None:
    _milliseconds(start)
    _milliseconds(end)
    if start >= end:
        raise ValueError("historical data start must be before end")


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)
