from __future__ import annotations

from math import ceil
from pathlib import Path

from kairospy.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairospy.data.products import BINANCE_USDM_PERPETUAL_HOURLY, BTC_OPTION_QUOTES_HOURLY, BTC_SPOT_DAILY

from .historical_archive import BinanceSpotArchiveProvider, BinanceUsdmPerpetualHourlyArchiveProvider
from .options_archive import BinanceOptionsEohArchiveProvider


class _RemovedDatasetAcquireMixin:
    def acquire(self, request: AcquisitionRequest):
        raise RuntimeError(
            "release publishing has been removed; use built-in Data Product ingestion backed by DatasetWriter"
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
