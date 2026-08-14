//! Public cross-process Reference contract.
//!
//! Reference exposes typed v2 events and a direct read-only SQLite reader.
//! It deliberately does not expose a shared-memory/mmap view capability.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod model;
pub mod projection;
pub mod sqlite;
pub mod transport;

pub use control::{
    ReferenceControlClient, ReferenceControlError, ReferenceControlRequest,
    ReferenceControlResponse,
};
pub use encode::{event_metadata, EncodeContext, ReferenceEncoder};
pub use error::{ContractError, ContractResult};
pub use event::{decode_event, ReferenceEvent, ReferenceEventFrame, ReferenceEventStream};
pub use projection::{ReferenceHealth, ReferenceMarket};
pub use sqlite::{
    ReferenceCatalogStats, ReferenceCollection, ReferenceMarketPage, ReferenceProjection,
    ReferenceSqliteReader, ReferenceWatermark, SqliteExecutionAccessQuery, SqliteInstrumentQuery,
    SqliteMarketDataAccessQuery, SqliteMarketQuery, REFERENCE_SQLITE_SCHEMA_VERSION,
};

use std::path::{Path, PathBuf};

/// Unified Reference client. SQLite is opened directly and read-only; it is
/// not represented as a mmap view.
pub struct ReferenceClient {
    control: ReferenceControlClient,
    control_socket: PathBuf,
    database_path: PathBuf,
    aeron_dir: Option<String>,
    aeron_channel: String,
    event_stream_id: i32,
}

pub struct ReferenceEndpoint {
    pub control_socket: PathBuf,
    pub database_path: PathBuf,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub event_stream_id: i32,
}

impl ReferenceClient {
    pub fn connect(endpoint: ReferenceEndpoint) -> Self {
        Self {
            control: ReferenceControlClient::connect(endpoint.control_socket.clone()),
            control_socket: endpoint.control_socket,
            database_path: endpoint.database_path,
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

    pub fn sqlite(&self) -> ContractResult<ReferenceSqliteReader> {
        ReferenceSqliteReader::open(&self.database_path)
    }

    pub fn control_socket(&self) -> &Path {
        &self.control_socket
    }
    pub fn database_path(&self) -> &Path {
        &self.database_path
    }
}
