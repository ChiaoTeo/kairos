//! v2 public cross-process contract for Market.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;
pub mod view;

pub use control::{
    MarketCommandEnvelope, MarketCommandStatus, MarketControlClient, MarketControlError,
    MarketControlResponse, MarketDataSource, MarketDataSourcesQuery, MarketDataSourcesResponse,
    MarketHealthResponse, MarketReleaseOwnerPayload, MarketReleaseOwnerResponse, MarketRestRequest,
    MarketRestResponse, MarketSubscribePayload, MarketSubscriptionResponse,
    MarketUnsubscribePayload,
};
pub use encode::{
    event_metadata, view_metadata, BarEncoder, EncodeContext, GreeksEncoder, OrderBookEncoder,
    QuoteEncoder, TradeEncoder,
};
pub use error::{ContractError, ContractResult};
pub use event::{MarketEvent, MarketEventFrame, MarketEventStream};
pub use view::{
    MarketViewKey, MarketViewKind, MarketViewPublisher, MarketViewReader, ViewFrame, ViewMetadata,
};

use std::path::PathBuf;

/// Unified public entry point. Control, events, and views remain separate
/// capabilities underneath this facade.
pub struct MarketClient {
    control: MarketControlClient,
    control_socket: PathBuf,
    view_root: PathBuf,
    aeron_dir: Option<String>,
    aeron_channel: String,
    event_stream_id: i32,
}

pub struct MarketEndpoint {
    pub control_socket: PathBuf,
    pub view_root: PathBuf,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub event_stream_id: i32,
}

impl MarketClient {
    pub fn connect(endpoint: MarketEndpoint) -> Self {
        Self {
            control: MarketControlClient::connect(endpoint.control_socket.clone()),
            control_socket: endpoint.control_socket,
            view_root: endpoint.view_root,
            aeron_dir: endpoint.aeron_dir,
            aeron_channel: endpoint.aeron_channel,
            event_stream_id: endpoint.event_stream_id,
        }
    }

    pub fn control(&self) -> &MarketControlClient {
        &self.control
    }

    pub fn events(&self, capacity: usize) -> ContractResult<MarketEventStream> {
        MarketEventStream::connect(
            self.aeron_dir.as_deref(),
            &self.aeron_channel,
            self.event_stream_id,
            capacity,
        )
    }

    pub fn view(&self, key: MarketViewKey) -> ContractResult<view::MarketViewReader> {
        view::MarketViewReader::open(&self.view_root, key)
    }

    pub fn control_socket(&self) -> &std::path::Path {
        &self.control_socket
    }
}
