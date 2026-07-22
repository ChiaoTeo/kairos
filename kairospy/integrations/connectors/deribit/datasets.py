from __future__ import annotations

from math import ceil
from pathlib import Path

from kairospy.data.acquisition import AcquisitionEstimate, AcquisitionRequest
from kairospy.data.products import BTC_DERIBIT_OPTION_QUOTES, BTC_DERIBIT_OPTION_TRADES, BTC_DVOL_DAILY

from .historical import DeribitDvolProvider
from .option_chain import DeribitOptionChainProvider
from .trade_history import DeribitOptionTradeHistoryProvider


class _RemovedDatasetAcquireMixin:
    provider = "deribit"

    def acquire(self, request: AcquisitionRequest):
        raise RuntimeError(
            "release publishing has been removed; use built-in Data Product ingestion backed by DatasetWriter"
        )

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(_days(request), cost_class="public")


class DeribitDvolDatasetConnector(_RemovedDatasetAcquireMixin):
    def __init__(self, root: str | Path = "data", archive: DeribitDvolProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or DeribitDvolProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_DVOL_DAILY.key)


class DeribitOptionTradesDatasetConnector(_RemovedDatasetAcquireMixin):
    def __init__(self, root: str | Path = "data", archive: DeribitOptionTradeHistoryProvider | None = None) -> None:
        self.root, self.archive = Path(root), archive or DeribitOptionTradeHistoryProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_DERIBIT_OPTION_TRADES.key)


class DeribitOptionSnapshotDatasetConnector(_RemovedDatasetAcquireMixin):
    def __init__(self, root: str | Path = "data", source: DeribitOptionChainProvider | None = None) -> None:
        self.root, self.source = Path(root), source or DeribitOptionChainProvider()

    def supports(self, logical_key: str) -> bool:
        return logical_key == str(BTC_DERIBIT_OPTION_QUOTES.key)

    def estimate(self, request: AcquisitionRequest) -> AcquisitionEstimate:
        return AcquisitionEstimate(1, cost_class="public")


def _days(request: AcquisitionRequest) -> int:
    return sum(max(1, ceil((item.end - item.start).total_seconds() / 86400)) for item in request.missing)
