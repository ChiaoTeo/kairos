mod cli;
pub(crate) mod conflux;
pub(crate) use conflux::ReferenceProjectionConfig;
mod connected;
mod model;
mod observations;
mod queries;
pub mod replay;
mod sources;
mod subscriptions;
mod universe;
pub use cli::{
    CliMarketApplication, CliMarketDiagnosticProvider, CliMarketHistoricalDataKind,
    CliMarketHistoricalDownloadRequest, CliMarketHistoricalMarketType, CliMarketHistoricalProvider,
};
pub use connected::{
    ConnectedMarketApplication, ConnectedMarketSourceQuery, ConnectedSourceAvailability,
};
pub use model::{
    ExecutionEstimate, MarketDataAvailability, MarketDataAvailabilityQuery, MarketError,
    MarketObservationResult, MarketQueryResult, OrderBookSide,
};
pub use replay::{load_replay_events, load_replay_events_many};
pub(crate) use sources::source_accepts;
pub(crate) use subscriptions::{
    OptionSelectionFilter, resolve_market, resolve_market_by_id, resolve_option_markets,
};
pub use universe::ReconcileMarketUniverse;

pub use crate::domain::events::{
    MarketChange, MarketEvent, MarketViewUpdate, OrderBookResyncRequired,
};
pub use crate::domain::freshness::{DataFreshnessStatus, FeedStatus, MarketFreshness};
pub use crate::domain::market::{MarketDataRoute, MarketSelectionQuery, ResolvedMarket};
pub use crate::domain::observation::order_book::{OrderBook, OrderBookDelta, PriceLevel};
pub use crate::domain::observation::{
    Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, MarketViewKey, ObservationKind,
    ObservationQualifier, ObservationScope, OpenInterest, OptionGreeks, Quote, QuoteBar, Rate,
    Ticker24h, Trade, TradeBar,
};
pub use crate::domain::source::{
    MarketReadiness, SourceDescriptor, SourceEpoch, SourceFailureKind, SourceId, SourceRouteKey,
    SourceState, SourceStatus,
};
pub use crate::domain::subscription::{
    ObservationSelector, ReconcileResult, SubscriptionId, SubscriptionMemberRequirement,
    SubscriptionMemberStatus, SubscriptionMode, SubscriptionState, SubscriptionStatus,
};
pub use crate::domain::view::{MarketView, MarketViewFreshness};

kairos_market_contract::market_control_rpc_conflux_actor! {
    pub trait MarketRpcActor;
    service MarketRpcService;
}

/// Public Market use-case facade around the sole mutable Market Actor.
pub struct MarketApplication {
    pub(crate) actor: crate::services::actor::MarketActor,
    pub(crate) conflux: conflux::MarketConfluxState,
}
