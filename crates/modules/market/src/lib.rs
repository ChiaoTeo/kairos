//! Market business boundary.
//!
//! The market actor is the sole owner of runtime market observations,
//! subscription resolution and freshness state. Provider connections and
//! wire publication and provider connections are selected in composition.

pub mod application;
pub mod composition;
mod domain;
mod services;

pub(crate) use application::ProviderRouteBinding;
pub use application::{
    Bar, CliDirectObservationResult, CliMarketApplication, CliMarketBarResult,
    CliMarketDatasetManifest, CliMarketDatasetsResult, CliMarketHistoricalDataKind,
    CliMarketHistoricalDownloadRequest, CliMarketHistoricalMarketType, CliMarketHistoricalProvider,
    CliMarketOnceProvider, CliMarketOnceRequest, CliMarketQuoteResult, CliMarketReplayResult,
    CliMarketReplaySnapshot, CliMarketRoutesResult, CliMarketValidationResult,
    ConnectedMarketApplication, ConnectedMarketOutput, ConnectedMarketRouteQuery,
    ConnectedRouteAvailability, DataFreshnessStatus, ExecutionEstimate, FeedStatus, FundingRate,
    IndexPrice, MarkPrice, MarketApplication, MarketChange, MarketDataRouteState, MarketError,
    MarketEvent, MarketFreshness, MarketObservation, MarketObservationError, MarketReadiness,
    MarketSelectionQuery, MarketView, MarketViewFreshness, MarketViewKey, MarketViewUpdate,
    ObservationIdentityError, ObservationKind, ObservationScope, ObservationSelector,
    ObservationSelectorError, OpenInterest, OptionGreeks, OrderBook, OrderBookDelta,
    OrderBookError, OrderBookResyncRequired, OrderBookSide, PriceLevel, Quote, QuoteBar, Rate,
    ReconcileMarketUniverse, ReconcileResult, ResolvedMarket, ResolvedMarketDataRoute,
    ResolvedMarketError, SubscriptionId, SubscriptionMemberRequirement, SubscriptionMemberStatus,
    SubscriptionMode, SubscriptionState, SubscriptionStatus, Ticker24h, Trade, TradeBar,
    load_replay_events, load_replay_events_many,
};
