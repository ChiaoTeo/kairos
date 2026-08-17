//! Public cross-process Reference contract.
//!
//! Reference exposes typed v2 events, a typed mmap current view, and control
//! commands. The SQLite reader is retained only for Reference-owned
//! persistence/event publication internals; business consumers use mmap.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;
pub mod view;

pub use control::{
    ReferenceControlClient, ReferenceControlError, ReferenceControlRequest,
    ReferenceControlResponse,
};
pub use encode::{event_metadata, EncodeContext, ReferenceEncoder};
pub use error::{ContractError, ContractResult};
pub use event::{decode_event, ReferenceEvent, ReferenceEventFrame, ReferenceEventStream};
pub use transport::{
    Asset, Entity, ExecutionAccess, FinancialProduct, Instrument, LifecycleEntry, Listing, Market,
    MarketDataAccess, ProviderHealthState, ReferenceLatestSnapshot,
};
pub use transport::{
    ReferenceCatalogStats, ReferenceCollection, ReferenceMarketPage, ReferenceProjection,
    ReferenceSqliteReader, ReferenceWatermark, SqliteExecutionAccessQuery, SqliteInstrumentQuery,
    SqliteMarketDataAccessQuery, SqliteMarketQuery, REFERENCE_SQLITE_SCHEMA_VERSION,
};
pub use transport::{ReferenceHealth, ReferenceMarket};
pub use view::{
    decode_reference_latest, encode_reference_latest, MmapReferenceLatestPublisher,
    ReferenceViewFrame, ReferenceViewKey, ReferenceViewKind, ReferenceViewReader,
};

use std::path::{Path, PathBuf};

/// Unified Reference client. Business reads use `view`; the database path is
/// retained only while Reference-owned persistence tooling migrates.
pub struct ReferenceClient {
    control: ReferenceControlClient,
    control_socket: PathBuf,
    view_root: PathBuf,
    actor_id: String,
    aeron_dir: Option<String>,
    aeron_channel: String,
    event_stream_id: i32,
}

pub struct ReferenceEndpoint {
    pub control_socket: PathBuf,
    pub view_root: PathBuf,
    pub actor_id: String,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub event_stream_id: i32,
}

impl ReferenceClient {
    pub fn connect(endpoint: ReferenceEndpoint) -> Self {
        Self {
            control: ReferenceControlClient::connect(endpoint.control_socket.clone()),
            control_socket: endpoint.control_socket,
            view_root: endpoint.view_root,
            actor_id: endpoint.actor_id,
            aeron_dir: endpoint.aeron_dir,
            aeron_channel: endpoint.aeron_channel,
            event_stream_id: endpoint.event_stream_id,
        }
    }

    pub fn control(&self) -> &ReferenceControlClient {
        &self.control
    }

    pub fn events(&self, capacity: usize) -> ContractResult<ReferenceEventStream> {
        ReferenceEventStream::connect(
            self.aeron_dir.as_deref(),
            &self.aeron_channel,
            self.event_stream_id,
            capacity,
        )
    }

    pub fn view(&self) -> ContractResult<ReferenceViewReader> {
        ReferenceViewReader::open(&self.view_root, ReferenceViewKey::latest(&self.actor_id))
    }

    pub fn control_socket(&self) -> &Path {
        &self.control_socket
    }
}
