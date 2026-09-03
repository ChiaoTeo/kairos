mod cli;
mod connected;
mod model;
mod observations;
pub(crate) mod process;
mod queries;
pub mod replay;
mod sources;
mod subscriptions;
mod universe;
pub use cli::{
    CliDirectObservationResult, CliMarketApplication, CliMarketBarResult,
    CliMarketDatasetCatalogEntry, CliMarketDatasetManifest, CliMarketDatasetsResult,
    CliMarketHistoricalDataKind, CliMarketHistoricalDownloadRequest, CliMarketHistoricalMarketType,
    CliMarketHistoricalProvider, CliMarketOnceProvider, CliMarketOnceRequest, CliMarketQuoteResult,
    CliMarketReplayResult, CliMarketReplaySnapshot, CliMarketRoute, CliMarketRouteResult,
    CliMarketRoutesResult, CliMarketValidationResult,
};
pub use connected::{
    ConnectedMarketApplication, ConnectedMarketOutput, ConnectedMarketRouteQuery,
    ConnectedRouteAvailability,
};
pub use model::{
    ExecutionEstimate, MarketDataAvailability, MarketDataAvailabilityQuery, MarketDataRouteState,
    MarketError, MarketObservationResult, MarketQueryResult, OrderBookSide,
};
pub(crate) use process::ReferenceDemandConfig;
pub use replay::{load_replay_events, load_replay_events_many};
pub use universe::ReconcileMarketUniverse;
pub(crate) use universe::{MarketProviderCapability, MarketUniverseResolver};

pub use crate::domain::events::{
    MarketChange, MarketEvent, MarketViewUpdate, OrderBookResyncRequired,
};
pub use crate::domain::freshness::{DataFreshnessStatus, FeedStatus, MarketFreshness};
pub(crate) use crate::domain::market::ProviderRouteBinding;
pub use crate::domain::market::{
    MarketSelectionQuery, ResolvedMarket, ResolvedMarketDataRoute, ResolvedMarketError,
};
pub use crate::domain::observation::order_book::{
    OrderBook, OrderBookDelta, OrderBookError, PriceLevel,
};
pub use crate::domain::observation::{
    Bar, FundingRate, IndexPrice, MarkPrice, MarketObservation, MarketObservationError,
    MarketViewKey, ObservationIdentityError, ObservationKind, ObservationQualifier,
    ObservationScope, OpenInterest, OptionGreeks, Quote, QuoteBar, Rate, Ticker24h, Trade,
    TradeBar,
};
pub use crate::domain::source::MarketReadiness;
pub use crate::domain::subscription::{
    ObservationSelector, ObservationSelectorError, ReconcileResult, SubscriptionId,
    SubscriptionMemberRequirement, SubscriptionMemberStatus, SubscriptionMode, SubscriptionState,
    SubscriptionStatus,
};
pub use crate::domain::view::{MarketView, MarketViewFreshness};

kairos_market_contract::market_control_rpc_conflux_actor! {
    pub trait MarketRpcActor;
    service MarketRpcService;
}

/// Public Market use-case facade around the sole mutable Market Actor.
pub struct MarketApplication {
    pub(crate) actor: crate::services::actor::MarketActor,
    pub(crate) conflux: process::MarketConfluxState,
}
