//! Closed, typed, single-writer runtime for long-running Kairos modules.
//!
//! A Conflux process exposes exactly one owning [`Contract`] and receives every
//! source through one closed [`ConfluxEvent`] enum. [`ConfluxSystem`] contains
//! the complete concrete client and connection universe. The runtime contains
//! no open resource catalog and no erased dispatch path.

mod actor;
mod context;
mod contract;
mod event;
mod lifecycle;
mod process;
mod resource;
mod system;

pub use actor::ConfluxActor;
pub use context::Context;
pub use contract::{Contract, RestContract, RestRequestOf, RestResponseOf};
pub use event::{
    ConfluxEvent, ContractEvent, IntegrationEvent, ManagedConnectionIdentity, SystemEvent,
};
pub use kairos_integration::blocking::{
    OrderCommand as BlockingOrderCommand, OrderQuery as BlockingOrderQuery,
};
pub use kairos_integration::composition::credentials::{
    load_workspace_credential, CredentialRecord, CredentialStore,
};
pub use kairos_integration::participants::binance::{
    advanced::stocks::{
        BinanceStocksRestConnection, BinanceStocksUserWebSocketConnection,
        BinanceStocksWebSocketConnection,
    },
    coinm::{
        BinanceCoinMRestConnection, BinanceCoinMUserWebSocketConnection,
        BinanceCoinMWebSocketConnection,
    },
    funding::BinanceFundingRestConnection,
    margin::{BinanceMarginRestConnection, BinanceMarginUserWebSocketConnection},
    options::{
        BinanceOptionsRestConnection, BinanceOptionsUserWebSocketConnection,
        BinanceOptionsWebSocketConnection,
    },
    spot::{
        BinanceSpotRestConnection, BinanceSpotUserWebSocketConnection,
        BinanceSpotWebSocketConnection,
    },
    usdm::{
        BinanceUsdMRestConnection, BinanceUsdMUserWebSocketConnection,
        BinanceUsdMWebSocketConnection,
    },
    BinanceCredential, BinanceRestConfig, BinanceUserWebSocketConfig, BinanceWebSocketConfig,
};
pub use kairos_integration::participants::hyperliquid::{
    info::HyperliquidInfoRestConnection, HyperliquidRestConfig, HyperliquidWebSocketConfig,
    HyperliquidWebSocketConnection,
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
    OkxPrivateRestConnection, OkxPrivateWebSocketConnection,
};
pub use kairos_integration::participants::okx::public::{
    OkxPublicRestConnection, OkxPublicWebSocketConnection,
};
pub use kairos_integration::participants::okx::{
    OkxCredential, OkxPrivateRestConfig, OkxPrivateWebSocketConfig, OkxRestConfig,
    OkxWebSocketConfig,
};
pub use kairos_integration::{
    AccountCredentialQuery, AccountMarketProfileQuery, AccountQuery, AccountStream, Bar,
    CommandOutcome, ConnectionDescriptor, ConnectionHealth, ConnectionHealthQuery, ConnectionKey,
    ConnectionLifecycle, ConnectionLifecycleCommand, ConnectionMaintenance, DecimalValue,
    ExecutionStream, ExternalAccountCredentialProfile, ExternalAccountEvent,
    ExternalAccountEventEnvelope, ExternalAccountIdentity, ExternalAccountModel,
    ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus, ExternalBalance,
    ExternalDecimal, ExternalEventEnvelope, ExternalExecutionEvent, ExternalInstrument,
    ExternalInstrumentCatalog, ExternalInstrumentKind, ExternalMarginMode, ExternalOpenOrder,
    ExternalOrder, ExternalOrderQuery, ExternalOrderStatus, ExternalParticipantEvent,
    ExternalPosition, ExternalPositionMode, HistoricalBarQuery, HistoricalBarRequest,
    HistoricalQuoteQuery, HistoricalTradeQuery, HistoricalWindow, IndeterminateCommand,
    InstrumentCatalogQuery, IntegrationError, MaintenanceOutcome, MarketBarQuery, MarketDataKind,
    MarketDataStream, MarketEvent, MarketEventKind, MarketFeed, MarketFundingRateQuery,
    MarketGreeksQuery, MarketIndexPriceQuery, MarketMarkPriceQuery, MarketOpenInterestQuery,
    MarketOrderBookQuery, MarketQuote, MarketQuoteQuery, MarketStatusQuery, MarketSubscription,
    MarketSubscriptionCommand, MarketSubscriptionId, MarketSubscriptionOutcome,
    MarketSubscriptionRequest, MarketTickerQuery, MarketTradeQuery, MarketVenueEvidence,
    OrderCommand, OrderEntryEvent, OrderEntryOptions, OrderEntryRequest, OrderEntryStatus,
    OrderQuery, OrderSide, OrderStatus, OrderType, ParticipantEventStream,
    ParticipantInstrumentRef, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    ParticipantRejection, TimeInForce,
};
pub use lifecycle::{ProcessPhase, ShutdownMode};
pub use process::{
    BuildError, Conflux, ConfluxConfig, ConfluxHandle, ConfluxOutcome, ConnectionControlError,
    HandleConnectionCollections, HandleError, OkxPrivateRestHandle, OkxPrivateWebSocketHandle,
    OkxPublicRestHandle, OkxPublicWebSocketHandle, RunError,
};
pub use resource::{
    ConnectionCreateOptions, EnsureDisposition, ManagedClient, ManagedClients, ManagedConnection,
    ManagedConnectionPolicy, ManagedConnections, ManagedResource, NamedResources, RecoveryPolicy,
    ResourceError, ResourceOperationError, ResourceState,
};
pub use system::{
    ConfluxSystem, ConnectionAccessError, ConnectionCollections, ConnectionCreateError,
    TypedConnectionCollection,
};
