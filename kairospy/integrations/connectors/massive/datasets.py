from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kairospy.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairospy.data.products import (
    US_EQUITY_MASSIVE_RAW_DAILY,
    US_EQUITY_MASSIVE_RAW_HOURLY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY,
    US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY,
    US_OPTION_MASSIVE_RAW_HOURLY,
)

from .client import MassiveClient
from .equity_daily_ohlcv import MassiveEquityDailyOhlcvPipeline
from .market_data import MassiveHistoricalMarketDataService
from .pipeline import MassiveOptionDataPipeline


@dataclass(frozen=True, slots=True)
class MassiveOptionProductConfig:
    logical_key: str
    underlying: str
    option_tickers: tuple[str, ...]
    underlying_reference_ticker: str | None = None

    def __post_init__(self) -> None:
        if not self.logical_key or not self.underlying or not self.option_tickers:
            raise ValueError("Massive option connector requires product, underlying and explicit option tickers")
        if any(not ticker.startswith("O:") for ticker in self.option_tickers):
            raise ValueError("Massive option tickers must use the O: namespace")


@dataclass(frozen=True, slots=True)
class MassiveEquityDailyOhlcvProductConfig:
    logical_key: str
    ticker: str
    view: str = "vendor_adjusted"

    def __post_init__(self) -> None:
        if not self.logical_key or not self.ticker:
            raise ValueError("Massive equity connector requires product and ticker")
        if self.view not in {"raw", "vendor_adjusted"}:
            raise ValueError("Massive equity view must be 'raw' or 'vendor_adjusted'")


class _RemovedDatasetAcquireMixin:
    provider = "massive"

    def acquire(self, request: AcquisitionRequest):
        raise RuntimeError(
            "release publishing has been removed; use built-in Data Product ingestion backed by DatasetWriter"
        )


class MassiveEquityDailyOhlcvDatasetConnector(_RemovedDatasetAcquireMixin):
    def __init__(self, root: str | Path, client: MassiveClient, config: MassiveEquityDailyOhlcvProductConfig) -> None:
        self.root, self.config = Path(root), config
        self.pipeline = MassiveEquityDailyOhlcvPipeline(root, client)

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days, cost_class="entitled-rest-bounded-ticker")


class MassiveEquityDailyMarketOhlcvDatasetConnector(_RemovedDatasetAcquireMixin):
    def __init__(self, root: str | Path, client: MassiveClient, *, view: str = "vendor_adjusted") -> None:
        if view == "adjusted":
            view = "vendor_adjusted"
        if view not in {"raw", "vendor_adjusted"}:
            raise ValueError("Massive equity daily view must be 'raw' or 'vendor_adjusted'")
        self.root = Path(root)
        self.client = client
        self.view = view
        self.market_data = MassiveHistoricalMarketDataService(self.root, client)

    @property
    def product(self):
        return US_EQUITY_MASSIVE_RAW_DAILY if self.view == "raw" else US_EQUITY_MASSIVE_VENDOR_ADJUSTED_DAILY

    @property
    def source(self):
        return self.market_data.source

    @source.setter
    def source(self, value) -> None:
        self.market_data.source = value

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(self.product.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        instruments = len(request.instruments) if request.instruments else 8000
        return AcquisitionEstimate(days * instruments, cost_class="entitled-rest-full-market-daily", instruments=instruments)

    def task_plan(self, request: AcquisitionRequest) -> dict[str, object]:
        return _task_plan(self.provider, "rest-paginated-aggregate", request, self.view)


class MassiveEquityHourlyOhlcvDatasetConnector(_RemovedDatasetAcquireMixin):
    def __init__(self, root: str | Path, client: MassiveClient, *, view: str = "adjusted") -> None:
        view = "adjusted" if view == "vendor_adjusted" else view
        if view not in {"raw", "adjusted"}:
            raise ValueError("Massive equity hourly view must be 'raw' or 'adjusted'")
        self.root = Path(root)
        self.client = client
        self.view = view
        self.market_data = MassiveHistoricalMarketDataService(self.root, client)

    @property
    def product(self):
        return US_EQUITY_MASSIVE_RAW_HOURLY if self.view == "raw" else US_EQUITY_MASSIVE_VENDOR_ADJUSTED_HOURLY

    @property
    def source(self):
        return self.market_data.source

    @source.setter
    def source(self, value) -> None:
        self.market_data.source = value

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(self.product.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        ranges = max(1, len(request.missing))
        instruments = len(request.instruments) if request.instruments else 8000
        return AcquisitionEstimate(ranges * instruments, cost_class="entitled-rest-full-market-hourly", instruments=instruments)

    def task_plan(self, request: AcquisitionRequest) -> dict[str, object]:
        return _task_plan(self.provider, "rest-paginated-aggregate", request, self.view)


class MassiveOptionHourlyOhlcvDatasetConnector(_RemovedDatasetAcquireMixin):
    minute_aggs_prefix = "us_options_opra/minute_aggs_v1"

    def __init__(self, root: str | Path, client: MassiveClient) -> None:
        self.root = Path(root)
        self.client = client
        self.market_data = MassiveHistoricalMarketDataService(self.root, client)

    @property
    def product(self):
        return US_OPTION_MASSIVE_RAW_HOURLY

    @property
    def source(self):
        return self.market_data.source

    @source.setter
    def source(self, value) -> None:
        self.market_data.source = value

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(US_OPTION_MASSIVE_RAW_HOURLY.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        ranges = max(1, len(request.missing))
        instruments = len(request.instruments) if request.instruments else 0
        return AcquisitionEstimate(ranges * max(1, instruments), cost_class="entitled-rest-explicit-option-hourly", instruments=instruments)

    def task_plan(self, request: AcquisitionRequest) -> dict[str, object]:
        return _task_plan(self.provider, "flat-file-minute-aggregate-to-hour" if not request.instruments else "rest-paginated-aggregate", request, "raw")


class MassiveOptionEventsDatasetConnector(_RemovedDatasetAcquireMixin):
    def __init__(
        self,
        root: str | Path,
        client: MassiveClient,
        config: MassiveOptionProductConfig,
        *,
        catalog_path: str | Path | None = None,
        mapping_path: str | Path | None = None,
    ) -> None:
        self.root, self.config = Path(root), config
        self.pipeline = MassiveOptionDataPipeline(root, client, catalog_path=catalog_path, mapping_path=mapping_path)

    def supports(self, logical_key: str) -> bool:
        return logical_key == self.config.logical_key

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        days = sum(max(1, (item.end.date() - item.start.date()).days + 1) for item in request.missing)
        return AcquisitionEstimate(days * len(self.config.option_tickers) * 3 + 6, cost_class="entitled")


def _task_plan(provider: str, task_type: str, request: AcquisitionRequest, view: str) -> dict[str, object]:
    ranges = [
        {
            "start": item.start.isoformat(),
            "end": item.end.isoformat(),
            "tasks": max(1, len(request.instruments) or 1),
            "cached": 0,
            "uncached": max(1, len(request.instruments) or 1),
        }
        for item in request.missing
    ]
    return {
        "provider": provider,
        "task_type": task_type,
        "universe": "bounded" if request.instruments else "full-market",
        "total_tasks": sum(int(item["tasks"]) for item in ranges),
        "cached_tasks": 0,
        "uncached_tasks": sum(int(item["uncached"]) for item in ranges),
        "resume_supported": True,
        "ranges": ranges,
        "metadata": {"view": view},
    }
