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
    load_replay_events, load_replay_events_many, ExecutionEstimate, MarketApplication,
    MarketCurrentFreshness, MarketCurrentView, MarketError, MarketProcess, MarketSnapshot,
    MarketSnapshotPublisher, OrderBookSide, ReconcileResult, ReferenceChangeSource, ReferenceEvent,
    SubscriptionState,
};
pub use domain::events::{MarketChange, MarketEvent, MarketViewUpdate, OrderBookResyncRequired};
pub use domain::freshness::FeedStatus;
pub use domain::freshness::{DataFreshnessStatus, MarketFreshness};
pub use domain::market::{MarketDescriptor, MarketSelectionQuery};
pub use domain::observations::{
    Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, OpenInterest, OptionGreeks, Quote,
    QuoteBar, Rate, Ticker24h, Trade, TradeBar,
};
pub use domain::orderbook::{OrderBook, OrderBookDelta, PriceLevel};
pub use domain::reference::ReferenceChanged;
pub use domain::source::{
    MarketReadiness, SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceRouteKey,
    SourceState, SourceStatus,
};
pub use domain::subscriptions::{
    SubscriptionId, SubscriptionMemberRequirement, SubscriptionMemberStatus, SubscriptionMode,
    SubscriptionStatus,
};
pub use domain::view::MarketViewKey;
