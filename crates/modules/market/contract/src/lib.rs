//! v2 public cross-process contract for Market.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;
pub mod view;

use std::path::PathBuf;

pub use control::{
    MarketCommandEnvelope, MarketCommandStatus, MarketControlClient, MarketControlError,
    MarketDataSource, MarketDataSourcesQuery, MarketDataSourcesResponse, MarketHealthResponse,
    MarketReleaseOwnerPayload, MarketReleaseOwnerResponse, MarketRestRequest, MarketRestResponse,
    MarketSubscribePayload, MarketSubscriptionResponse, MarketUnsubscribePayload,
};
pub use encode::{
    BarEncoder, EncodeContext, GreeksEncoder, OrderBookEncoder, QuoteEncoder, TradeEncoder,
    event_metadata, view_metadata,
};
pub use error::{ContractError, ContractResult};
pub use event::{MarketEvent, MarketEventFrame, MarketEventPublisher, MarketEventStream};
pub use kairos_transport::AeronEndpoint;
pub use view::{
    MarketViewKey, MarketViewKind, MarketViewPublisher, MarketViewReader, ViewFrame, ViewMetadata,
    market_view_path,
};

/// Unified public entry point. Control, events, and views remain separate
/// capabilities underneath this facade.
pub struct MarketClient {
    control: MarketControlClient,
    control_socket: PathBuf,
    view_root: PathBuf,
    events: AeronEndpoint,
}

pub struct MarketEndpoint {
    pub control_socket: PathBuf,
    pub view_root: PathBuf,
    pub events: AeronEndpoint,
}

impl MarketClient {
    pub fn connect(endpoint: MarketEndpoint) -> Self {
        Self {
            control: MarketControlClient::connect(endpoint.control_socket.clone()),
            control_socket: endpoint.control_socket,
            view_root: endpoint.view_root,
            events: endpoint.events,
        }
    }

    pub fn control(&self) -> &MarketControlClient {
        &self.control
    }

    pub fn events(&self, capacity: usize) -> ContractResult<MarketEventStream> {
        MarketEventStream::connect(&self.events, capacity)
    }

    pub fn view(&self, key: MarketViewKey) -> ContractResult<view::MarketViewReader> {
        view::MarketViewReader::open(&self.view_root, key)
    }

    pub fn control_socket(&self) -> &std::path::Path {
        &self.control_socket
    }
}
