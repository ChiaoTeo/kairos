//! Market business boundary.
//!
//! The market actor is the sole owner of runtime market observations,
//! subscription resolution and freshness state. Provider connections and
//! wire publication and provider connections are selected in composition.

pub mod application;
pub mod composition;
mod domain;
mod services;

pub use application::{
    load_replay_events, load_replay_events_many, Bar, DataFreshnessStatus, ExecutionEstimate,
    FeedStatus, FundingRate, IndexPrice, MarkPrice, MarketApplication, MarketChange,
    MarketChangePublisher, MarketDataRoute, MarketError, MarketEvent, MarketFreshness,
    MarketObservation, MarketProcess, MarketReadiness, MarketSelectionQuery, MarketView,
    MarketViewFreshness, MarketViewKey, MarketViewUpdate, ObservationKind, ObservationScope,
    ObservationSelector, OpenInterest, OptionGreeks, OrderBook, OrderBookDelta,
    OrderBookResyncRequired, OrderBookSide, PriceLevel, Quote, QuoteBar, Rate,
    ReconcileMarketUniverse, ReconcileResult, ResolvedMarket, SourceDescriptor, SourceEpoch,
    SourceFailureKind, SourceId, SourceRouteKey, SourceState, SourceStatus, SubscriptionId,
    SubscriptionMemberRequirement, SubscriptionMemberStatus, SubscriptionMode, SubscriptionState,
    SubscriptionStatus, Ticker24h, Trade, TradeBar,
};
