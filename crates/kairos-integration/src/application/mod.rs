//! Stable public application facade for integration.
//!
//! Provider-specific factories return concrete connection facades.  The
//! application module exposes only interaction patterns that are stable
//! across providers; provider selection and registry mechanics stay private.

pub mod account;
pub mod account_inspection;
pub(crate) mod connection;
pub mod earn;
pub(crate) mod error;
pub mod execution_stream;
pub mod market;
pub(crate) mod market_stream;
pub mod order_query;
pub mod reference;
pub mod transfer;

pub use crate::domain::ConnectionSpec;
pub use crate::domain::{
    AccessScope, AssetType, IntegrationCapability, ProductFamily, TransportKind,
};
pub use account::{
    AccountEventStreamConnection, AccountMarketProfileConnection, AccountReadConnection,
    BufferedIntegrationAccountStream, ExternalMarketProfile, ExternalMarketProfileRequest,
    IntegrationAccountStream,
};
pub use account_inspection::{
    AccountCredentialInspectionConnection, ExternalAccountCredentialProfile,
};
pub use connection::{Connection, OrderEntryConnection};
pub use earn::{
    EarnActionResult, EarnConnection, EarnPosition, EarnProduct, EarnProductType,
    EarnRedeemRequest, EarnReward, EarnSubscribeRequest,
};
pub use error::IntegrationError;
pub use execution_stream::{ExecutionStreamConnection, ExternalExecutionEvent};
pub use market::{
    MarketEvent, MarketEventKind, MarketStreamConnection, MarketSubscription, SubscriptionId,
};
pub use order_query::{ExternalOrder, ExternalOrderQuery, OrderQueryConnection};
pub use transfer::{TransferConnection, TransferRequest, TransferResult};
