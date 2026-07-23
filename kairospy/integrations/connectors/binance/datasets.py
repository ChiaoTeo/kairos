from __future__ import annotations

from datetime import timedelta
from math import ceil
from pathlib import Path

from kairospy.integrations.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairospy.integrations.data_products.binance import (
    BINANCE_USDM_PERPETUAL_HOURLY,
    BTC_OPTION_QUOTES_HOURLY,
    BTC_SPOT_DAILY,
)

from .historical_archive import BinanceSpotArchiveProvider, BinanceUsdmPerpetualHourlyArchiveProvider
from .options_archive import BinanceOptionsEohArchiveProvider


class _RemovedDatasetAcquireMixin:
    def acquire(self, request: AcquisitionRequest):
        raise RuntimeError(
            "release publishing has been removed; use integration-provided Data Product ingestion backed by DatasetWriter"
        )


class BinanceSpotDatasetConnector(_RemovedDatasetAcquireMixin):
    provider = "binance"

    def __init__(self, root: str | Path = "data", archive: BinanceSpotArchiveProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or BinanceSpotArchiveProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_SPOT_DAILY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")


class BinanceUsdmPerpetualHourlyDatasetConnector(_RemovedDatasetAcquireMixin):
    provider = "binance"

    def __init__(
        self,
        root: str | Path = "data",
        archive: BinanceUsdmPerpetualHourlyArchiveProvider | None = None,
    ) -> None:
        self.root = Path(root)
        self.archive = archive or BinanceUsdmPerpetualHourlyArchiveProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BINANCE_USDM_PERPETUAL_HOURLY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        months = sum(max(1, ceil((item.end - item.start).total_seconds() / (86400 * 28))) for item in request.missing)
        estimate_symbols = getattr(self.archive, "estimated_symbol_count", lambda _root: 700)
        instruments = len(request.instruments) if request.instruments else estimate_symbols(self.root / "source")
        return AcquisitionEstimate(months * instruments, cost_class="public", instruments=instruments)

    def task_plan(self, request: AcquisitionRequest) -> dict[str, object]:
        symbols = tuple(request.instruments) or getattr(self.archive, "discover_symbols", lambda _root: ())(
            self.root / "source"
        )
        return {
            "provider": "binance",
            "task_type": "public-archive-zip",
            "universe": "bounded" if request.instruments else "full-market",
            "symbols": len(symbols),
            "total_tasks": max(1, len(symbols)) * max(1, len(request.missing)),
            "cached_tasks": 0,
            "uncached_tasks": max(1, len(symbols)) * max(1, len(request.missing)),
            "resume_supported": True,
            "ranges": [
                {"start": item.start.isoformat(), "end": item.end.isoformat(), "tasks": max(1, len(symbols))}
                for item in request.missing
            ],
        }

    def ingest_dataset(self, request: AcquisitionRequest) -> dict[str, object]:
        from kairospy.data import DatasetStore, DatasetWriter

        rows: list[dict[str, object]] = []
        source_root = self.root / "source"
        symbols = tuple(request.instruments) or getattr(self.archive, "discover_symbols", lambda _root: ())(source_root)
        for item in request.missing:
            rows.extend(
                _usdm_perpetual_hourly_rows(
                    self.archive.fetch(symbols, item.start, item.end, source_root),
                )
            )
        dataset_id = str(BINANCE_USDM_PERPETUAL_HOURLY.key)
        store = DatasetStore(self.root)
        fields = list(rows[0].keys()) if rows else []
        store.ensure_dataset(dataset_id, metadata={
            "primary_time": BINANCE_USDM_PERPETUAL_HOURLY.product.primary_time,
            "fields": fields,
            "data_product": dataset_id,
            "provider": self.provider,
            "venue": "binance",
            "source": {
                "source_kind": "public-archive",
                "provider": self.provider,
                "dataset": "usdm_klines",
            },
        })
        if rows:
            DatasetWriter(store).append(
                dataset_id,
                rows,
                partition_by=("symbol", "event_day"),
                time_field=BINANCE_USDM_PERPETUAL_HOURLY.product.primary_time,
            )
        return {
            "dataset": dataset_id,
            "row_count": len(rows),
            "symbols": list(symbols),
            "fields": fields,
        }


class BinanceOptionQuotesDatasetConnector(_RemovedDatasetAcquireMixin):
    provider = "binance"

    def __init__(self, root: str | Path = "data", archive: BinanceOptionsEohArchiveProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or BinanceOptionsEohArchiveProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_OPTION_QUOTES_HOURLY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")


def _days(request: AcquisitionRequest) -> int:
    return sum(max(1, ceil((item.end - item.start).total_seconds() / 86400)) for item in request.missing)


def _usdm_perpetual_hourly_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for row in rows:
        symbol = str(row["symbol"])
        period_start = row["period_start"]
        period_end = period_start + timedelta(hours=1)
        output.append({
            "period_start": period_start,
            "period_end": period_end,
            "event_time": period_end,
            "available_time": period_end,
            "event_day": period_start.date().isoformat(),
            "venue": "binance-usdm",
            "instrument_id": f"crypto:binance:perpetual:{symbol}",
            "symbol": symbol,
            "product": "usdm-perpetual",
            "interval": "PT1H",
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["volume"]),
            "quote_volume": float(row["quote_volume"]),
            "trade_count": int(row["trade_count"]),
            "taker_buy_base_volume": float(row["taker_buy_base_volume"]),
            "taker_buy_quote_volume": float(row["taker_buy_quote_volume"]),
            "source": "binance",
        })
    return output
