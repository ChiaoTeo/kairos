mod model;
mod observations;
mod process;
mod queries;
pub mod replay;
mod sources;
mod subscriptions;
mod universe;
pub use crate::domain::{
    events::{MarketChange, MarketEvent, MarketViewUpdate, OrderBookResyncRequired},
    freshness::{DataFreshnessStatus, FeedStatus, MarketFreshness},
    market::{MarketDataRoute, MarketSelectionQuery, ResolvedMarket},
    observation::{
        order_book::{OrderBook, OrderBookDelta, PriceLevel},
        Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, MarketViewKey, ObservationKind,
        ObservationQualifier, ObservationScope, OpenInterest, OptionGreeks, Quote, QuoteBar, Rate,
        Ticker24h, Trade, TradeBar,
    },
    source::{
        MarketReadiness, SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId,
        SourceRouteKey, SourceState, SourceStatus,
    },
    subscription::{
        ObservationSelector, ReconcileResult, SubscriptionId, SubscriptionMemberRequirement,
        SubscriptionMemberStatus, SubscriptionMode, SubscriptionState, SubscriptionStatus,
    },
    view::{MarketView, MarketViewFreshness},
};
pub use model::{
    ExecutionEstimate, MarketDataAvailability, MarketDataAvailabilityQuery, MarketError,
    MarketObservationResult, MarketQueryResult, OrderBookSide,
};
pub(crate) use process::MarketProcessSettings;
pub use process::{MarketChangePublisher, MarketProcess};
pub use replay::{load_replay_events, load_replay_events_many};
pub(crate) use sources::source_accepts;
pub(crate) use subscriptions::{resolve_market, resolve_option_markets};
pub use universe::ReconcileMarketUniverse;

/// Public Market use-case facade around the sole mutable Market Actor.
pub struct MarketApplication {
    pub(crate) actor: crate::services::actor::MarketActor,
}
