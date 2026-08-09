//! Market business boundary.
//!
//! The market actor is the sole owner of runtime market observations,
//! subscription resolution and freshness state. Provider connections and
//! wire publication and provider connections are selected in composition.

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{
    MarketApplication, MarketError, MarketProcess, MarketRuntime, MarketSnapshot,
    MarketSnapshotPublisher, ReconcileResult, ReferenceChangeSource, ReferenceEvent,
    SubscriptionState,
};
pub use composition::{binance_spot_rest_feed, binance_spot_websocket_feed};
pub use domain::freshness::FeedStatus;
pub use domain::market::{MarketDescriptor, MarketSelectionQuery};
pub use domain::observations::{Bar, MarketObservation, OptionGreeks, Quote, Trade};
pub use domain::orderbook::{OrderBook, OrderBookDelta, PriceLevel};
pub use domain::reference::ReferenceChanged;
pub use domain::subscriptions::{SubscriptionId, SubscriptionMode};
pub use domain::view::MarketViewKey;
