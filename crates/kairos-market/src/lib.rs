//! Market business boundary.
//!
//! The market actor is the sole owner of runtime market observations,
//! subscription resolution and freshness state. Provider connections and
//! wire publication are injected behind application-owned protocols.

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{MarketApplication, MarketError, MarketProcess};
pub use composition::{binance_spot_rest_feed, binance_spot_websocket_feed};
pub use domain::freshness::FeedStatus;
pub use domain::market::{MarketDescriptor, MarketSelectionQuery};
pub use domain::observations::{MarketObservation, Quote, Trade};
pub use domain::orderbook::{OrderBook, OrderBookDelta, PriceLevel};
pub use domain::reference::ReferenceChanged;
pub use domain::subscriptions::{SubscriptionId, SubscriptionMode};
pub use services::actor::{MarketSnapshot, ReconcileResult};
