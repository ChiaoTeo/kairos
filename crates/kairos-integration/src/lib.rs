//! Exchange integration boundary.
//!
//! Public APIs describe connection capabilities and provider-neutral request
//! infrastructure. Concrete gateways and drivers remain implementation
//! details behind the integration module boundary.

pub mod application;
pub mod credentials;
pub mod domain;
mod integration;
pub mod protocol;
pub(crate) mod services;

/// Optional CCXT client-adapter contract.
///
/// This is the only provider-specific extension exposed by the crate: callers
/// may supply a CCXT market loader to [`Integration::with_ccxt_reference`].
/// The concrete connection and factory remain private.
pub mod ccxt {
    pub use crate::services::gateways::ccxt::market::{CcxtMarketClient, CcxtMarketRow};
}

pub use application::OrderEntryConnection;
pub use application::{
    AccountEventStreamConnection, AccountMarketProfileConnection, AccountReadConnection,
    BufferedIntegrationAccountStream, Connection, ConnectionSpec, EarnActionResult, EarnConnection,
    EarnPosition, EarnProduct, EarnProductType, EarnRedeemRequest, EarnReward,
    EarnSubscribeRequest, IntegrationAccountStream, IntegrationError, MarketEvent, MarketEventKind,
    MarketStreamConnection, MarketSubscription, SubscriptionId, TransferConnection,
    TransferRequest, TransferResult,
};
pub use domain::{DecimalValue, OrderEntryEvent, OrderEntryRequest, OrderEntryStatus};
pub use integration::Integration;
