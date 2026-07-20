from __future__ import annotations

"""Compatibility exports for the split Binance connector modules."""

from .execution_gateway import (
    BINANCE_FUTURES_EXECUTION_CAPABILITIES,
    BINANCE_OPTIONS_EXECUTION_CAPABILITIES,
    BINANCE_SPOT_EXECUTION_CAPABILITIES,
    BinanceExecutionAdapter,
    BinanceExecutionGateway,
    BinanceOptionsExecutionAdapter,
    BinanceOptionsExecutionGateway,
)
from .account_gateway import (
    BinanceAccountAdapter,
    BinanceAccountGateway,
    BinanceOptionsAccountAdapter,
    BinanceOptionsAccountGateway,
)
from .funding_settlement import BinanceFundingAdapter, BinanceFundingSettlementClient
from .market_data_client import (
    BINANCE_FUTURES_MARKET_DATA_CAPABILITIES,
    BINANCE_OPTIONS_MARKET_DATA_CAPABILITIES,
    BINANCE_SPOT_MARKET_DATA_CAPABILITIES,
    BinanceMarketDataAdapter,
    BinanceMarketDataClient,
)
from .market_stream import (
    BinanceStreamSession,
    WebSocketClientConnection,
    WebSocketClientConnector,
    WebSocketConnection,
    WebSocketConnector,
    parse_market_stream_event,
    websocket_url,
)
from .option_market_snapshot import OptionMarketSnapshot, parse_option_market_snapshot
from .order_recovery import BinanceRecoveryService, RecoverySnapshot
from .reference_data import (
    BINANCE_FUTURES_REFERENCE_CAPABILITIES,
    BINANCE_OPTIONS_REFERENCE_CAPABILITIES,
    BINANCE_SPOT_REFERENCE_CAPABILITIES,
    BinanceFuturesReferenceAdapter,
    BinanceFuturesReferenceDataClient,
    BinanceOptionsReferenceAdapter,
    BinanceOptionsReferenceDataClient,
    BinanceSpotReferenceAdapter,
    BinanceSpotReferenceDataClient,
)
from .request_signing import BinanceSigner, synchronize_clock
from .rest_transport import BinanceTransport, RateLimiter, UrllibBinanceTransport
from .user_data_stream import (
    BalanceUpdate,
    BinanceUserDataStreamService,
    BinanceUserStreamProcessor,
    UserFillUpdate,
    parse_user_stream_event,
)
