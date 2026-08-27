//! v2 public cross-process contract for Market.

extern crate self as kairos_market_contract;

use std::path::Path;

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod view;

pub use control::{
    MarketCommandEnvelope, MarketCommandOutcome, MarketCommandStatus, MarketControlError,
    MarketControlRpcClient, MarketControlRpcServer, MarketDataRoute, MarketDataRouteState,
    MarketDataRoutesQuery, MarketDataRoutesResponse, MarketFeedStatus, MarketHealthResponse,
    MarketHealthStatus, MarketOperation, MarketReleaseOwnerPayload, MarketReleaseOwnerResponse,
    MarketSubscribePayload, MarketSubscriptionResponse, MarketSubscriptionState, MarketTarget,
    MarketUnsubscribePayload, ObservationRequirement, ProviderPreference, SubscriptionOwnerKey,
    SubscriptionPendingReason,
};
pub use encode::{EncodeContext, event_metadata};
pub use error::{ContractError, ContractResult};
pub use event::{
    MarketEvent, MarketEventFrame, MarketEventPublisher, MarketEventStream, decode_event,
};
pub type MarketConnection = kairos_protocol::ContractClient;
pub const MARKET_EVENTS_STREAM_ID: i32 = kairos_transport::stream_ids::MARKET_EVENTS;
pub const DEFAULT_AERON_CHANNEL: &str = kairos_transport::DEFAULT_CHANNEL;
pub const CONTRACT_FINGERPRINT: &str = "kairos.market.contract.v2";
pub use kairos_indexed_view::MetadataSnapshot as IndexedViewMetadata;
pub use kairos_transport::AeronEndpoint;
pub use view::{
    MARKET_BARS_DATABASE, MARKET_FRESHNESS_DATABASE, MARKET_FUNDING_RATES_DATABASE,
    MARKET_GREEKS_DATABASE, MARKET_INDEX_PRICES_DATABASE, MARKET_MAP_SIZE,
    MARKET_MARK_PRICES_DATABASE, MARKET_OPEN_INTEREST_DATABASE, MARKET_ORDER_BOOKS_DATABASE,
    MARKET_QUOTES_DATABASE, MARKET_RATES_DATABASE, MARKET_RESOURCE_EPOCH, MARKET_TICKERS_DATABASE,
    MarketBarCurrent, MarketBarKind, MarketCurrentEvidence, MarketFreshnessCurrent,
    MarketFreshnessStatus, MarketFundingRateCurrent, MarketGreeksCurrent, MarketIndexPriceCurrent,
    MarketIndexedSnapshot, MarketIndexedValue, MarketIndexedView, MarketMarkPriceCurrent,
    MarketObservationScope, MarketOpenInterestCurrent, MarketOrderBookCurrent,
    MarketOrderBookLevel, MarketQuoteCurrent, MarketRateCurrent, MarketTicker24hCurrent,
    MarketViewKey, MarketViewKind, market_database, market_indexed_environment_path,
    market_indexed_identity, market_indexed_key, market_indexed_schema_set,
};

#[derive(Clone)]
pub struct MarketClient {
    inner: kairos_protocol::ContractClient,
}

impl MarketClient {
    pub fn connect(connection: MarketConnection) -> Self {
        Self { inner: connection }
    }
    pub fn control(&self) -> impl MarketControlRpcClient + '_ {
        self.inner.control()
    }
    pub fn events(&self, capacity: usize) -> ContractResult<MarketEventStream> {
        MarketEventStream::connect(
            self.inner
                .require_aeron_endpoint()
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            capacity,
        )
    }
    pub fn indexed_current(
        &self,
        identity: &kairos_primitives::runtime::InstanceIdentity,
    ) -> ContractResult<MarketIndexedView> {
        MarketIndexedView::open(self.require_view_root()?, identity)
    }
    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
