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

use std::path::PathBuf;

use kairos_primitives::runtime::ActorId;

pub use control::{
    ReferenceControlError, ReferenceControlRpcClient, ReferenceControlRpcServer,
    ReferenceHealthResponse, ReferenceHealthStatus, ReferenceMutationResponse,
    ReferenceOptionCoverageRequest, ReferenceOptionCoverageResponse, ReferenceProviderHealth,
    ReferenceProviderStatus, ReferencePublishResponse, ReferenceRefreshResponse,
    ReferenceSourceControlRequest, ReferenceSourceStatusResponse, UpsertAssetRequest,
    UpsertInstrumentRequest, UpsertListingRequest,
};
pub use encode::{EncodeContext, ReferenceEncoder, event_metadata};
pub use error::{ContractError, ContractResult};
pub use event::{
    ReferenceEvent, ReferenceEventFrame, ReferenceEventPublisher, ReferenceEventStream,
    decode_event,
};
pub use kairos_transport::AeronEndpoint;
pub use transport::{
    Asset, Entity, Instrument, LifecycleEntry, Listing, Market, ProviderHealthState,
    REFERENCE_SQLITE_SCHEMA_VERSION, ReferenceCatalogStats, ReferenceCollection, ReferenceHealth,
    ReferenceMarket, ReferenceMarketPage, ReferenceProjection, ReferenceProjectionSnapshot,
    ReferenceSqliteReader, ReferenceWatermark, SqliteInstrumentQuery, SqliteMarketQuery,
};

/// Unified Reference client. Business reads use consumer-scoped SQLite queries.
pub struct ReferenceClient {
    database: PathBuf,
    actor_id: ActorId,
    events: AeronEndpoint,
}

pub struct ReferenceEndpoint {
    pub database: PathBuf,
    pub actor_id: ActorId,
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
        ReferenceSqliteReader::open(&self.database)?.market_snapshot(self.actor_id.as_str())
    }

    pub fn execution_snapshot(&self) -> ContractResult<ReferenceProjectionSnapshot> {
        ReferenceSqliteReader::open(&self.database)?.execution_snapshot(self.actor_id.as_str())
    }

    pub fn account_snapshot(&self) -> ContractResult<ReferenceProjectionSnapshot> {
        ReferenceSqliteReader::open(&self.database)?.account_snapshot(self.actor_id.as_str())
    }
}
