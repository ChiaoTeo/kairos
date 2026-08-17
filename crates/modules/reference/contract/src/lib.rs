//! Public cross-process Reference contract.
//!
//! Reference exposes typed v2 events, contract-owned read-only SQLite queries,
//! and control commands. Reference remains the only database writer; business
//! consumers never depend on its tables or persistence records.

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;

pub use control::{
    ReferenceControlClient, ReferenceControlError, ReferenceControlRequest,
    ReferenceControlResponse,
};
pub use encode::{event_metadata, EncodeContext, ReferenceEncoder};
pub use error::{ContractError, ContractResult};
pub use event::{decode_event, ReferenceEvent, ReferenceEventFrame, ReferenceEventStream};
pub use transport::{
    Asset, Entity, ExecutionAccess, Instrument, LifecycleEntry, Listing, Market, MarketDataAccess,
    ProviderHealthState, ReferenceProjectionSnapshot,
};
pub use transport::{
    ReferenceCatalogStats, ReferenceCollection, ReferenceMarketPage, ReferenceProjection,
    ReferenceSqliteReader, ReferenceWatermark, SqliteExecutionAccessQuery, SqliteInstrumentQuery,
    SqliteMarketDataAccessQuery, SqliteMarketQuery, REFERENCE_SQLITE_SCHEMA_VERSION,
};
pub use transport::{ReferenceHealth, ReferenceMarket};

use std::path::PathBuf;

/// Unified Reference client. Business reads use consumer-scoped SQLite queries.
pub struct ReferenceClient {
    database: PathBuf,
    actor_id: String,
    aeron_dir: Option<String>,
    aeron_channel: String,
    event_stream_id: i32,
}

pub struct ReferenceEndpoint {
    pub database: PathBuf,
    pub actor_id: String,
    pub aeron_dir: Option<String>,
    pub aeron_channel: String,
    pub event_stream_id: i32,
}

impl ReferenceClient {
    pub fn connect(endpoint: ReferenceEndpoint) -> Self {
        Self {
            database: endpoint.database,
            actor_id: endpoint.actor_id,
            aeron_dir: endpoint.aeron_dir,
            aeron_channel: endpoint.aeron_channel,
            event_stream_id: endpoint.event_stream_id,
        }
    }

    pub fn events(&self, capacity: usize) -> ContractResult<ReferenceEventStream> {
        ReferenceEventStream::connect(
            self.aeron_dir.as_deref(),
            &self.aeron_channel,
            self.event_stream_id,
            capacity,
        )
    }

    pub fn watermark(&self) -> ContractResult<ReferenceWatermark> {
        ReferenceSqliteReader::open(&self.database)?.watermark()
    }

    pub fn market_snapshot(&self) -> ContractResult<ReferenceProjectionSnapshot> {
        ReferenceSqliteReader::open(&self.database)?.market_snapshot(&self.actor_id)
    }

    pub fn execution_snapshot(&self) -> ContractResult<ReferenceProjectionSnapshot> {
        ReferenceSqliteReader::open(&self.database)?.execution_snapshot(&self.actor_id)
    }

    pub fn account_snapshot(&self) -> ContractResult<ReferenceProjectionSnapshot> {
        ReferenceSqliteReader::open(&self.database)?.account_snapshot(&self.actor_id)
    }
}
