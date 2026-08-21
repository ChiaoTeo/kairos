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
    MarketControlRpcClient, MarketControlRpcServer, MarketDataSource, MarketDataSourcesQuery,
    MarketDataSourcesResponse, MarketFeedStatus, MarketHealthResponse, MarketHealthStatus,
    MarketOperation, MarketReleaseOwnerPayload, MarketReleaseOwnerResponse, MarketSourceStatus,
    MarketSubscribePayload, MarketSubscriptionResponse, MarketSubscriptionStatus,
    MarketUnsubscribePayload, SubscriptionOwnerKey,
};
pub use encode::{
    BarEncoder, EncodeContext, GreeksEncoder, OrderBookEncoder, QuoteEncoder, TradeEncoder,
    event_metadata, view_metadata,
};
pub use error::{ContractError, ContractResult};
pub use event::{MarketEvent, MarketEventFrame, MarketEventPublisher, MarketEventStream};
pub type MarketConnection = kairos_protocol::ContractClient;

pub use kairos_transport::AeronEndpoint;
use kairos_transport::SnapshotEnvelopeMetadata;
pub use view::{
    MarketViewKey, MarketViewKind, MarketViewPublisher, ViewFrame, ViewMetadata, market_view_path,
};

/// Unified public entry point. Control, events, and views remain separate
/// capabilities underneath this facade.
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

    pub fn quote(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<Quote> {
        Quote::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::Quote, qualifier)?,
        )
    }

    pub fn bar_window(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<BarWindow> {
        BarWindow::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::BarWindow, qualifier)?,
        )
    }

    pub fn order_book(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<OrderBook> {
        OrderBook::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::OrderBook, qualifier)?,
        )
    }

    pub fn freshness(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<Freshness> {
        Freshness::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::Freshness, qualifier)?,
        )
    }

    pub fn greeks(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<Greeks> {
        Greeks::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::Greeks, qualifier)?,
        )
    }

    pub fn rate(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<Rate> {
        Rate::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::Rate, qualifier)?,
        )
    }

    pub fn ticker_24h(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<Ticker24h> {
        Ticker24h::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::Ticker24h, qualifier)?,
        )
    }

    pub fn mark_price(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<MarkPrice> {
        MarkPrice::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::MarkPrice, qualifier)?,
        )
    }

    pub fn funding_rate(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<FundingRate> {
        FundingRate::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::FundingRate, qualifier)?,
        )
    }

    pub fn open_interest(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<OpenInterest> {
        OpenInterest::open(
            self.require_view_root()?,
            MarketViewKey::new(
                scope_key,
                source_id,
                MarketViewKind::OpenInterest,
                qualifier,
            )?,
        )
    }

    pub fn index_price(
        &self,
        scope_key: impl Into<String>,
        source_id: impl AsRef<str>,
        qualifier: Option<impl Into<String>>,
    ) -> ContractResult<IndexPrice> {
        IndexPrice::open(
            self.require_view_root()?,
            MarketViewKey::new(scope_key, source_id, MarketViewKind::IndexPrice, qualifier)?,
        )
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

macro_rules! market_view_handle {
    ($handle:ident, $snapshot:ident, $view:ty, $decode:ident) => {
        pub struct $handle {
            reader: view::MarketViewReader,
        }

        impl $handle {
            fn open(root: &Path, key: MarketViewKey) -> ContractResult<Self> {
                Ok(Self {
                    reader: view::MarketViewReader::open(root, key)?,
                })
            }

            pub fn read(&self) -> ContractResult<$snapshot> {
                Ok($snapshot {
                    frame: self.reader.read()?,
                })
            }

            pub fn key(&self) -> &MarketViewKey {
                self.reader.key()
            }
        }

        pub struct $snapshot {
            frame: ViewFrame,
        }

        impl $snapshot {
            pub fn generation(&self) -> u64 {
                self.frame.generation()
            }

            pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
                self.frame.envelope_metadata()
            }

            pub fn view(&self) -> ContractResult<$view> {
                self.frame.$decode()
            }
        }
    };
}

market_view_handle!(Quote, QuoteSnapshot, view::QuoteLatestView<'_>, quote);
market_view_handle!(BarWindow, BarWindowSnapshot, view::BarWindowView<'_>, bar);
market_view_handle!(
    OrderBook,
    OrderBookSnapshot,
    view::OrderBookLatestView<'_>,
    order_book
);
market_view_handle!(
    Freshness,
    FreshnessSnapshot,
    view::FreshnessView<'_>,
    freshness
);
market_view_handle!(Greeks, GreeksSnapshot, view::GreeksLatestView<'_>, greeks);
market_view_handle!(Rate, RateSnapshot, view::RateLatestView<'_>, rate);
market_view_handle!(
    Ticker24h,
    Ticker24hSnapshot,
    view::Ticker24hLatestView<'_>,
    ticker_24h
);
market_view_handle!(
    MarkPrice,
    MarkPriceSnapshot,
    view::MarkPriceLatestView<'_>,
    mark_price
);
market_view_handle!(
    FundingRate,
    FundingRateSnapshot,
    view::FundingRateLatestView<'_>,
    funding_rate
);
market_view_handle!(
    OpenInterest,
    OpenInterestSnapshot,
    view::OpenInterestLatestView<'_>,
    open_interest
);
market_view_handle!(
    IndexPrice,
    IndexPriceSnapshot,
    view::IndexPriceLatestView<'_>,
    index_price
);
