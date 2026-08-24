//! Closed, typed, single-writer runtime for long-running Kairos modules.
//!
//! A Conflux process receives every source through one closed [`ConfluxEvent`]
//! enum. [`ConfluxSystem`] contains the complete concrete client and connection
//! universe. The runtime contains no open resource catalog and no erased
//! dispatch path.

mod actor;
mod context;
mod control;
mod event;
mod lifecycle;
mod output;
mod process;
mod resource;
mod rpc;
mod system;

pub use actor::ConfluxActor;
pub use context::Context;
pub use control::{JsonRpcConfluxRuntime, JsonRpcRuntimeConfig, JsonRpcRuntimeError};
pub use event::{
    ConfluxEvent, ContractEvent, IntegrationEvent, ManagedConnectionIdentity, SystemEvent,
};
pub use kairos_integration::blocking::{
    OrderCommand as BlockingOrderCommand, OrderQuery as BlockingOrderQuery,
};
pub use kairos_integration::composition::credentials::{
    CredentialRecord, CredentialStore, credential_secret_ref, load_workspace_credential,
};
pub use kairos_integration::participants::binance::advanced::portfolio::BinancePortfolioMarginRestConnection;
pub use kairos_integration::participants::binance::advanced::portfolio::pro::BinancePortfolioMarginProRestConnection;
pub use kairos_integration::participants::binance::advanced::stocks::{
    BinanceStocksRestConnection, BinanceStocksUserWebSocketConnection,
    BinanceStocksWebSocketConnection,
};
pub use kairos_integration::participants::binance::capital::{
    BinanceCapitalRestConfig, BinanceCapitalRestConnection, BinanceSubAccountCapitalRestConfig,
    BinanceSubAccountCapitalRestConnection, BinanceSubAccountIdentity, BinanceTransferAccount,
};
pub use kairos_integration::participants::binance::coinm::{
    BinanceCoinMRestConnection, BinanceCoinMUserWebSocketConnection,
    BinanceCoinMWebSocketConnection,
};
pub use kairos_integration::participants::binance::earn::BinanceSimpleEarnRestConnection;
pub use kairos_integration::participants::binance::funding::BinanceFundingRestConnection;
pub use kairos_integration::participants::binance::margin::{
    BinanceMarginRestConnection, BinanceMarginUserWebSocketConnection,
};
pub use kairos_integration::participants::binance::options::{
    BinanceOptionsRestConnection, BinanceOptionsUserWebSocketConnection,
    BinanceOptionsWebSocketConnection,
};
pub use kairos_integration::participants::binance::spot::{
    BinanceSpotRestConnection, BinanceSpotUserWebSocketConnection, BinanceSpotWebSocketConnection,
};
pub use kairos_integration::participants::binance::usdm::{
    BinanceUsdMRestConnection, BinanceUsdMUserWebSocketConnection, BinanceUsdMWebSocketConnection,
};
pub use kairos_integration::participants::binance::{
    BinanceCredential, BinanceHistoryQuery, BinanceRestConfig, BinanceUserWebSocketConfig,
    BinanceWebSocketConfig,
};
pub use kairos_integration::participants::hyperliquid::info::HyperliquidInfoRestConnection;
pub use kairos_integration::participants::hyperliquid::{
    HyperliquidRestConfig, HyperliquidWebSocketConfig, HyperliquidWebSocketConnection,
};
pub use kairos_integration::participants::ibkr::{
    IbkrAccountQueryConfig, IbkrAccountQueryConnection, IbkrAccountStreamConfig,
    IbkrAccountStreamConnection, IbkrExecutionStreamConfig, IbkrExecutionStreamConnection,
    IbkrMarketDataConfig, IbkrMarketDataConnection, IbkrOrderConfig, IbkrOrderConnection,
};
pub use kairos_integration::participants::massive::{
    InstrumentQuery as MassiveInstrumentQuery, MassiveOptionsWebSocketConnection,
    MassiveRestConfig, MassiveRestConnection, MassiveStocksWebSocketConnection,
    MassiveWebSocketConfig,
};
pub use kairos_integration::participants::okx::private::{
    OkxHistoryQuery, OkxPrivateRestConnection, OkxPrivateWebSocketConnection,
};
pub use kairos_integration::participants::okx::public::{
    OkxPublicRestConnection, OkxPublicWebSocketConnection,
};
pub use kairos_integration::participants::okx::{
    OkxCredential, OkxPrivateRestConfig, OkxPrivateWebSocketConfig, OkxRestConfig,
    OkxWebSocketConfig,
};
pub use kairos_integration::{
    AccountCredentialQuery, AccountMarketProfileQuery, AccountProfileQuery, AccountQuery,
    AccountStream, AssetTransferCommand, AssetTransferQuery, AssetTransferRequest,
    AssetTransferState, AssetTransferStatus, AssetTransferStatusQuery, AssetTransferSubmission,
    Bar, CommandOutcome, CommandResult, ConnectionDescriptor, ConnectionHealth,
    ConnectionHealthQuery, ConnectionKey, ConnectionLifecycle, ConnectionLifecycleCommand,
    ConnectionMaintenance, DecimalValue, EarnActionKind, EarnActionQuery, EarnActionState,
    EarnActionStatus, EarnActionStatusQuery, EarnCommand, EarnLiquidity, EarnPage, EarnPosition,
    EarnPositionState, EarnPositionsRequest, EarnProduct, EarnProductFamily, EarnProductQuery,
    EarnProductsRequest, EarnRateObservation, EarnRatesRequest, EarnRedeemRequest,
    EarnRedemptionAmount, EarnRedemptionChannel, EarnRedemptionOption, EarnReward,
    EarnRewardsRequest, EarnSubmission, EarnSubscribeRequest, EarnSubscriptionEligibility,
    EarnSubscriptionPreview, EarnSubscriptionPreviewRequest, ExecutionStream,
    ExternalAccountCredentialProfile, ExternalAccountEvent, ExternalAccountEventEnvelope,
    ExternalAccountIdentity, ExternalAccountInfo, ExternalAccountModel, ExternalAccountProfile,
    ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus, ExternalBalance,
    ExternalDecimal, ExternalEventEnvelope, ExternalExecutionEvent, ExternalFeeComponent,
    ExternalFeeDiscount, ExternalFeeSchedule, ExternalFeeScheduleRequest, ExternalInstrument,
    ExternalInstrumentCatalog, ExternalInstrumentKind, ExternalMarginMode, ExternalOpenOrder,
    ExternalOrder, ExternalOrderQuery, ExternalOrderStatus, ExternalParticipantEvent,
    ExternalPosition, ExternalPositionMode, FeeQuery, HistoricalBarQuery, HistoricalBarRequest,
    HistoricalQuoteQuery, HistoricalTradeQuery, HistoricalWindow, IndeterminateCommand,
    InstrumentCatalogQuery, IntegrationError, MaintenanceOutcome, MarketBar, MarketBarQuery,
    MarketBarRequest, MarketDataKind, MarketDataStream, MarketEvent, MarketEventKind, MarketFeed,
    MarketFundingRateQuery, MarketGreeks, MarketGreeksQuery, MarketIndexPriceQuery,
    MarketMarkPriceQuery, MarketOpenInterestQuery, MarketOrderBook, MarketOrderBookQuery,
    MarketOrderBookRequest, MarketQuote, MarketQuoteQuery, MarketStatusQuery, MarketSubscription,
    MarketSubscriptionCommand, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest, MarketTickerQuery, MarketTrade, MarketTradeQuery,
    MarketVenueEvidence, OrderCommand, OrderEntryEvent, OrderEntryOptions, OrderEntryRequest,
    OrderEntryStatus, OrderQuery, OrderSide, OrderStatus, OrderType, ParticipantEventStream,
    ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    ParticipantRejection, TimeInForce,
};
pub use kairos_transport::{
    AeronEndpoint, DEFAULT_CHANNEL as DEFAULT_AERON_CHANNEL, stream_ids as output_stream_ids,
};
pub use lifecycle::{ProcessPhase, ShutdownMode};
pub use output::{
    AeronOutputDeclaration, AeronOutputs, FileOutputDeclaration, FileOutputs,
    MmapOutputDeclaration, MmapOutputs, OutputCollections, OutputCreateError, OutputPublishError,
    SnapshotEnvelopeMetadata,
};
pub use process::{
    BuildError, Conflux, ConfluxConfig, ConfluxHandle, ConfluxOutcome, ConnectionControlError,
    HandleConnectionCollections, HandleError, OkxPrivateRestHandle, OkxPrivateWebSocketHandle,
    OkxPublicRestHandle, OkxPublicWebSocketHandle, RpcActorInvocation, RunError,
};
pub use resource::{
    ConnectionCreateOptions, EnsureDisposition, ManagedClient, ManagedClients,
    ManagedConnectionPolicy, ManagedResource, NamedResources, RecoveryPolicy, ResourceError,
    ResourceOperationError, ResourceState,
};
pub use rpc::ConfluxJsonRpcService;
pub use system::{
    ConfluxSystem, ConnectionAccessError, ConnectionCollections, ConnectionCreateError,
    TypedConnectionCollection, reference_connection_from_workspace,
};
