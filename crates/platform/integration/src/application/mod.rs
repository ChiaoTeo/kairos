//! Stable public application facade for integration.
//!
//! Provider-specific factories return concrete connection facades.  The
//! application module exposes only interaction patterns that are stable
//! across providers; provider selection and registry mechanics stay private.

pub mod blocking;
pub mod capabilities;
pub mod credential;
pub(crate) mod error;
pub mod external_event;
pub mod outcome;
pub mod participants;

pub use crate::domain::{
    ConnectionDescriptor, ConnectionDomainRef, ConnectionHealth, ConnectionLifecycle,
    ConnectionState, ParticipantInstrumentTypeRef, ParticipantKind, ParticipantRef,
    ProviderInstrumentRef,
};
pub use capabilities::account::{
    AsyncAccountCredentialInspectionConnection, ExternalAccountCredentialProfile,
};
pub use capabilities::account::{
    AsyncAccountEventSource, AsyncAccountMarketProfileConnection, AsyncAccountReadConnection,
    ExternalMarketProfile, ExternalMarketProfileRequest,
};
pub use capabilities::account_facts::{
    ExternalAccountEvent, ExternalAccountEventEnvelope, ExternalAccountModel,
    ExternalAccountSegment, ExternalAccountSnapshot, ExternalAccountStatus, ExternalBalance,
    ExternalDecimal, ExternalFillEvent, ExternalMarginMode, ExternalOpenOrder, ExternalOrderEvent,
    ExternalOrderStatus, ExternalPosition, ExternalPositionMode,
};
pub use capabilities::execution::{
    AsyncOrderEntryConnection, AsyncOrderEventSource, AsyncOrderQueryConnection,
    ExternalExecutionEvent, ExternalOrder, ExternalOrderQuery,
};
pub use capabilities::execution_facts::{
    DecimalValue, ExecutionReport, Order, OrderEntryEvent, OrderEntryOptions, OrderEntryRequest,
    OrderEntryStatus, OrderRequest, OrderSide, OrderStatus, OrderType, TimeInForce,
};
pub use capabilities::funding::{
    AsyncEarnConnection, EarnActionResult, EarnPosition, EarnProduct, EarnProductType,
    EarnRedeemRequest, EarnReward, EarnSubscribeRequest,
};
pub use capabilities::funding::{AsyncTransferConnection, TransferRequest, TransferResult};
pub use capabilities::market::{
    AsyncHistoricalMarketDataConnection, AsyncMarketEventSource, AsyncMarketQuoteConnection,
    AsyncMarketSnapshotConnection, HistoricalMarketRequest, MarketEvent, MarketEventKind,
    MarketSubscription, SubscriptionId,
};
pub use capabilities::market_facts::{
    MarketBar, MarketDataKind, MarketGreeks, MarketQuote, MarketStreamCapabilities, MarketTrade,
};
pub use error::IntegrationError;
pub use external_event::ExternalEventEnvelope;
pub use outcome::{
    CommandOutcome, CommandResult, DeliveryCertainty, IndeterminateCommand, ProviderRejection,
};

// Synchronous contracts stay crate-visible so participant implementations can
// implement them without making `application::*` a second public blocking API.
pub(crate) use capabilities::account::{
    AccountCredentialInspectionConnection, AccountEventReceive, AccountEventStreamConnection,
    AccountMarketProfileConnection, AccountReadConnection,
};
pub(crate) use capabilities::execution::{
    OrderEntryConnection, OrderEventSource, OrderQueryConnection,
};
pub(crate) use capabilities::market::HistoricalMarketDataConnection;
