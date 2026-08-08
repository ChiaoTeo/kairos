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
pub use domain::{IntegrationRoute, ParticipantKind, ParticipantRef};
pub use integration::Integration;
