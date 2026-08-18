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
    ReferenceControlResponse, ReferenceHealthResponse, ReferenceMutationResponse,
    ReferenceOptionCoverageRequest, ReferenceOptionCoverageResponse, ReferenceProviderHealth,
    ReferencePublishResponse, ReferenceRefreshResponse, ReferenceRestRequest,
    ReferenceRestResponse, ReferenceSourceControlRequest, ReferenceSourceStatusResponse,
    UpsertAssetRequest, UpsertInstrumentRequest, UpsertListingRequest,
};
pub use encode::{event_metadata, EncodeContext, ReferenceEncoder};
pub use error::{ContractError, ContractResult};
pub use event::{
    decode_event, ReferenceEvent, ReferenceEventFrame, ReferenceEventPublisher,
    ReferenceEventStream,
};
pub use kairos_transport::AeronEndpoint;
pub use transport::{
    Asset, Entity, Instrument, LifecycleEntry, Listing, Market, ProviderHealthState,
    ReferenceProjectionSnapshot,
};
pub use transport::{
    ReferenceCatalogStats, ReferenceCollection, ReferenceMarketPage, ReferenceProjection,
    ReferenceSqliteReader, ReferenceWatermark, SqliteInstrumentQuery, SqliteMarketQuery,
    REFERENCE_SQLITE_SCHEMA_VERSION,
};
pub use transport::{ReferenceHealth, ReferenceMarket};

use std::path::PathBuf;

/// Unified Reference client. Business reads use consumer-scoped SQLite queries.
pub struct ReferenceClient {
    database: PathBuf,
    actor_id: String,
    events: AeronEndpoint,
}

pub struct ReferenceEndpoint {
    pub database: PathBuf,
    pub actor_id: String,
    pub events: AeronEndpoint,
}

impl ReferenceClient {
    pub fn connect(endpoint: ReferenceEndpoint) -> Self {
        Self {
            database: endpoint.database,
            actor_id: endpoint.actor_id,
            events: endpoint.events,
        }
    }

    pub fn events(&self, capacity: usize) -> ContractResult<ReferenceEventStream> {
        ReferenceEventStream::connect(&self.events, capacity)
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
