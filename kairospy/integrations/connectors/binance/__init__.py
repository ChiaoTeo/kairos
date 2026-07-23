from .account_gateway import BinanceAccountGateway, BinanceOptionsAccountGateway
from .funding_settlement import BinanceFundingSettlementClient
from .execution_gateway import (
    BinanceExecutionGateway,
    BinanceOptionsExecutionGateway,
)
from .market_data_client import BinanceMarketDataClient
from .market_stream import BinanceStreamSession, WebSocketClientConnector, websocket_url
from .option_market_snapshot import OptionMarketSnapshot, parse_option_market_snapshot
from .order_recovery import BinanceRecoveryService, RecoverySnapshot
from .user_data_stream import (
    BalanceUpdate,
    BinanceUserDataStreamService,
    BinanceUserFillEventSource,
    BinanceUserStreamProcessor,
    UserFillUpdate,
    parse_user_stream_event,
)
from .reference_data import (
    BinanceFuturesReferenceDataClient,
    BinanceOptionsReferenceDataClient,
    BinanceSpotReferenceDataClient,
)
from .request_signing import BinanceSigner, synchronize_clock
from .rest_transport import BinanceTransport, RateLimiter, UrllibBinanceTransport
from .order_book import (
    BinanceOrderBookSnapshotProvider,
    BinanceOrderBookSyncFault,
    BinanceOrderBookSyncMetrics,
    BinanceOrderBookSyncService,
    OrderBookSnapshotProvider,
)
from .stream import BinanceCanonicalStreamService

__all__ = [
    "BinanceAccountGateway",
    "BinanceExecutionGateway",
    "BinanceFundingSettlementClient",
    "BinanceFuturesReferenceDataClient",
    "BinanceMarketDataClient",
    "BinanceOptionsAccountGateway",
    "BinanceOptionsExecutionGateway",
    "BinanceOptionsReferenceDataClient",
    "BinanceSigner",
    "BinanceSpotReferenceDataClient",
    "BinanceTransport",
    "BinanceUserDataStreamService",
    "BinanceUserFillEventSource",
    "BinanceUserStreamProcessor",
    "UserFillUpdate",
    "RateLimiter",
    "UrllibBinanceTransport",
    "WebSocketClientConnector",
    "websocket_url",
    "BinanceCanonicalStreamService",
    "BinanceRuntimeFeed",
    "BinanceRuntimeFeedFactory",
    "BinanceOrderBookSnapshotProvider",
    "BinanceOrderBookSyncFault",
    "BinanceOrderBookSyncMetrics",
    "BinanceOrderBookSyncService",
    "OrderBookSnapshotProvider",
    "synchronize_clock",
]


def __getattr__(name: str):
    if name in {"BinanceRuntimeFeed", "BinanceRuntimeFeedFactory"}:
        from .runtime_feed import BinanceRuntimeFeed, BinanceRuntimeFeedFactory
        return {"BinanceRuntimeFeed": BinanceRuntimeFeed, "BinanceRuntimeFeedFactory": BinanceRuntimeFeedFactory}[name]
    raise AttributeError(name)
