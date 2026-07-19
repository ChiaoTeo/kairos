from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.backtest.calendar import TradingCalendar
from trading.reference import ProviderId, ReferenceCatalog, ReferenceCatalogRepository
from trading.storage.data_lake import write_json

from .client import MassiveClient
from .source import MassiveSourceArchive


class MassiveEquityDayAggPipeline:
    def __init__(self, lake_root: str | Path, client: MassiveClient, *, mapping_path: str | Path | None = None) -> None:
        self.root = Path(lake_root)
        self.source = MassiveSourceArchive(self.root, client)
        self.calendar = TradingCalendar()
        path = Path(mapping_path) if mapping_path is not None else self.root / "reference" / "catalog.json"
        self.mappings = ReferenceCatalogRepository(path).load() if path.exists() else ReferenceCatalog()

    def prepare(self, dataset_id: str, ticker: str, start: date, end: date, *, view: str = "vendor_adjusted") -> dict[str, object]:
        ticker = ticker.strip().upper()
        if not ticker or not start < end:
            raise ValueError("equity day aggregates require ticker and [start,end)")
        if view not in {"raw", "vendor_adjusted"}:
            raise ValueError("equity day aggregate view must be 'raw' or 'vendor_adjusted'")
        adjusted = view == "vendor_adjusted"
        archive = self.source.fetch_pages(
            f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
            {"adjusted": adjusted, "sort": "asc", "limit": 50000},
        )
        rows = []
        for raw in self.source.iter_results(archive):
            window_start = datetime.fromtimestamp(int(raw["t"]) / 1000, tz=timezone.utc)
            trading_day = window_start.astimezone(self.calendar.timezone).date()
            if not start <= trading_day < end:
                continue
            session = self.calendar.session(trading_day)
            instrument_id = self._instrument_id(ticker, session.closes_at.astimezone(timezone.utc))
            rows.append({
                "ticker": ticker, "instrument_id": instrument_id, "price_view": view,
                "event_date": trading_day,
                "window_start": window_start, "available_time": session.closes_at.astimezone(timezone.utc),
                "open": Decimal(str(raw["o"])), "high": Decimal(str(raw["h"])),
                "low": Decimal(str(raw["l"])), "close": Decimal(str(raw["c"])),
                "volume": Decimal(str(raw.get("v", 0))),
                "transactions": int(raw.get("n", 0)),
                "vwap": Decimal(str(raw["vw"])) if raw.get("vw") is not None else None,
            })
        rows.sort(key=lambda item: item["event_date"])
        if not rows:
            raise ValueError(f"Massive returned no {ticker} daily aggregates")
        if len({item["event_date"] for item in rows}) != len(rows):
            raise ValueError("equity day aggregates contain duplicate trading dates")

        pa, pq = _pyarrow()
        target = self.root / "canonical/market/ohlcv/asset_class=equity/region=us/provider=massive/interval=1d" / f"view={view}" / f"dataset={dataset_id}"
        manifest_path = target / "manifest.json"
        row_hash = sha256(json.dumps(rows, default=_json_default, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("content_sha256") == row_hash:
                return existing
            raise ValueError(f"dataset ID {dataset_id!r} already refers to different content")
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"part-{row_hash[:24]}.parquet"
        pq.write_table(pa.Table.from_pylist(rows, schema=_schema(pa)), path, compression="zstd", use_dictionary=True)
        manifest = {
            "manifest_version": 1, "dataset_id": dataset_id, "ticker": ticker,
            "source": "massive.stocks.daily_aggregates", "view": view, "adjusted": adjusted,
            "boundary": "[start,end)",
            "start": start.isoformat(), "end": end.isoformat(), "rows": len(rows),
            "content_sha256": row_hash, "file": path.name, "file_sha256": _file_hash(path),
            "source_receipt": str((archive.directory / "receipt.json").relative_to(self.root)),
        }
        write_json(target / "lineage.json", {
            "provider": "massive", "api_base": "https://api.massiveprivateserver.site",
            "resource": archive.receipt["resource"], "source_receipt": manifest["source_receipt"],
            "source": {"content_sha256": row_hash, "receipt": manifest["source_receipt"]},
            "visibility": "US securities session close", "view": view, "adjusted": adjusted,
            "parameters": {"ticker": ticker, "start": start.isoformat(), "end": end.isoformat(), "adjusted": adjusted},
        })
        write_json(target / "coverage.json", {
            "start": datetime.combine(start, time.min, timezone.utc).isoformat(),
            "end": datetime.combine(end, time.min, timezone.utc).isoformat(),
            "boundary": "[start,end)",
            "rows": len(rows), "calendar": "US_SECURITIES", "ticker_count": 1,
            "tickers": [ticker], "view": view,
        })
        write_json(target / "schema.json", {
            "schema_id": "market.ohlcv.equity.us.1d.v1", "schema_version": 1,
            "primary_key": ["instrument_id", "event_date", "price_view"],
            "primary_time": "available_time",
            "fields": [field.name for field in _schema(pa)],
        })
        write_json(target / "quality.json", {
            "publishable": True, "quality_level": "Q2", "duplicate_dates": 0, "invalid_ohlc": 0,
            "known_limitations": [
                "bounded single-ticker smoke dataset; not a full-market point-in-time universe",
                "provider ticker is still used as the provisional instrument identity",
            ],
        })
        write_json(manifest_path, manifest)
        return manifest

    def _instrument_id(self, ticker: str, at: datetime) -> str:
        try:
            return self.mappings.resolve_provider_symbol(ProviderId("massive"), "stocks", ticker, at).target_id
        except LookupError:
            return f"equity:us:{ticker}"


def _schema(pa):
    decimal = pa.decimal128(24, 8)
    return pa.schema([
        ("ticker", pa.string()), ("instrument_id", pa.string()), ("price_view", pa.string()),
        ("event_date", pa.date32()),
        ("window_start", pa.timestamp("ms", tz="UTC")), ("available_time", pa.timestamp("ms", tz="UTC")),
        ("open", decimal), ("high", decimal), ("low", decimal), ("close", decimal),
        ("volume", decimal), ("transactions", pa.int64()), ("vwap", decimal),
    ])


def _file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_default(value: object):
    if isinstance(value, (date, datetime, Decimal)):
        return value.isoformat() if isinstance(value, (date, datetime)) else str(value)
    raise TypeError(type(value).__name__)


def _pyarrow():
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as error:
        raise RuntimeError("equity day aggregates require the 'data' optional dependency") from error
    return pa, pq
