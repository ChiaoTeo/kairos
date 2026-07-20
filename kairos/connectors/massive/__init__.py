"""Massive connector public surface with Kairos naming."""

from kairos.adapters.massive.client import MassiveClient, MassiveError, MassiveResponse, UrllibMassiveTransport
from kairos.adapters.massive.close_implied_volatility import OptionCloseImpliedVolatilityPipeline
from kairos.adapters.massive.config import MassiveConfig
from kairos.adapters.massive.corporate_actions import MassiveCorporateActionDecoder
from kairos.adapters.massive.curated import MassiveCuratedSliceBuilder, MassiveMarketSnapshotBuilder
from kairos.adapters.massive.daily_ohlcv import OptionDailyOhlcvPipeline, SpxwDailyOhlcvPipeline
from kairos.adapters.massive.datasets import (
    MassiveEquityDailyOhlcvDatasetConnector,
    MassiveEquityDailyOhlcvProductConfig,
)
from kairos.adapters.massive.entitlement_diagnostics import (
    MassiveEntitlementDiagnostics,
    MassiveEntitlementReport,
)
from kairos.adapters.massive.equity_daily_ohlcv import MassiveEquityDailyOhlcvPipeline
from kairos.adapters.massive.equity_identity import MassiveEquityIdentityResolver, MassiveEquityIdentityResult
from kairos.adapters.massive.reference_pipeline import MassiveReferencePipeline
from kairos.adapters.massive.reference_store import MassiveReferenceStore
from kairos.adapters.massive.vendor_archive import (
    MassiveFlatFileBatchDownloader,
    MassiveFlatFileClient,
    MassiveVendorArchiveClient,
    OutsideDownloadWindow,
)
from kairos.adapters.massive.websocket import (
    MassiveCanonicalStreamService,
    MassiveLiveStream,
    MassiveStreamFault,
    MassiveWebSocketClient,
)

__all__ = [
    "MassiveCanonicalStreamService",
    "MassiveClient",
    "MassiveConfig",
    "MassiveCorporateActionDecoder",
    "MassiveCuratedSliceBuilder",
    "MassiveEntitlementDiagnostics",
    "MassiveEntitlementReport",
    "MassiveEquityDailyOhlcvDatasetConnector",
    "MassiveEquityDailyOhlcvPipeline",
    "MassiveEquityDailyOhlcvProductConfig",
    "MassiveEquityIdentityResolver",
    "MassiveEquityIdentityResult",
    "MassiveError",
    "MassiveFlatFileBatchDownloader",
    "MassiveFlatFileClient",
    "MassiveLiveStream",
    "MassiveMarketSnapshotBuilder",
    "MassiveReferencePipeline",
    "MassiveReferenceStore",
    "MassiveResponse",
    "MassiveStreamFault",
    "MassiveVendorArchiveClient",
    "MassiveWebSocketClient",
    "OptionCloseImpliedVolatilityPipeline",
    "OptionDailyOhlcvPipeline",
    "OutsideDownloadWindow",
    "SpxwDailyOhlcvPipeline",
    "UrllibMassiveTransport",
]
