//! Public cross-process Reference contract.
//!
//! Reference exposes typed v2 events, contract-owned read-only SQLite queries,
//! and control commands. Reference remains the only database writer; business
//! consumers never depend on its tables or persistence records.

extern crate self as kairos_reference_contract;

pub mod control;
pub mod encode;
pub mod error;
pub mod event;
pub mod transport;

use std::path::PathBuf;

pub use control::{
    ReferenceAppPhase, ReferenceAppRuntimeError, ReferenceAppRuntimeStatus,
    ReferenceCatalogIntegrityStatus, ReferenceCatalogReadiness, ReferenceCatalogRuntimeStatus,
    ReferenceControlError, ReferenceControlRpcClient, ReferenceControlRpcServer,
    ReferenceCoverageRuntimeStatus, ReferenceDiagnostic, ReferenceDiagnosticSeverity,
    ReferenceHealthResponse, ReferenceHealthStatus, ReferenceMutationResponse,
    ReferenceOptionCoverageRequest, ReferenceOptionCoverageResponse, ReferenceProviderHealth,
    ReferenceProviderProduct, ReferenceProviderStatus, ReferencePublicationRuntimeError,
    ReferencePublicationRuntimeStatus, ReferencePublishResponse, ReferenceRefreshResponse,
    ReferenceRuntimeStatus, ReferenceRuntimeStatusResponse, ReferenceSourceControlRequest,
    ReferenceSourceDefinitionRequest, ReferenceSourceDesiredState, ReferenceSourceKind,
    ReferenceSourcePhase, ReferenceSourceProgress, ReferenceSourceProgressKind,
    ReferenceSourceRuntimeError, ReferenceSourceRuntimeStatus, ReferenceSourceScope,
    ReferenceSourceScopeKind, ReferenceSourceScopeRequest, ReferenceSourceScopeResponse,
    ReferenceSourceStatusResponse, ReferenceSourceSyncPolicy, ReferenceSourceTickBudget,
    ReferenceSourceWorkItem, ReferenceUpsertConflictPolicy, ReferenceUpsertProvenance,
    UpsertAssetRequest, UpsertInstrumentRequest, UpsertListingRequest,
};
pub use encode::{EncodeContext, ReferenceEncoder, event_metadata};
pub use error::{ContractError, ContractResult};
pub use event::{
    ReferenceEvent, ReferenceEventFrame, ReferenceEventPublisher, ReferenceEventStream,
    decode_event,
};
use kairos_primitives::runtime::ActorId;
pub use kairos_transport::AeronEndpoint;
pub use transport::{
    Asset, Entity, Instrument, LifecycleEntry, Listing, Market, ProviderHealthState,
    REFERENCE_SQLITE_SCHEMA_VERSION, ReferenceCatalogStats, ReferenceCatalogStatus,
    ReferenceCollection, ReferenceHealth, ReferenceIntegrityStats, ReferenceMarket,
    ReferenceMarketPage, ReferenceProjection, ReferenceProjectionSnapshot, ReferenceSqliteReader,
    ReferenceWatermark, SqliteInstrumentQuery, SqliteMarketQuery,
};

/// Unified Reference client. Business reads use consumer-scoped SQLite queries.
#[derive(Clone)]
pub struct ReferenceClient {
    inner: kairos_protocol::ContractClient,
    database: PathBuf,
    actor_id: ActorId,
}

#[derive(Clone)]
pub struct ReferenceConnection {
    pub contract: kairos_protocol::ContractClient,
    pub database: PathBuf,
    pub actor_id: ActorId,
}

impl ReferenceClient {
    pub fn connect(connection: ReferenceConnection) -> Self {
        Self {
            inner: connection.contract,
            database: connection.database,
            actor_id: connection.actor_id,
        }
    }

    pub fn control(&self) -> impl ReferenceControlRpcClient + '_ {
        self.inner.control()
    }

    pub fn events(&self, capacity: usize) -> ContractResult<ReferenceEventStream> {
        ReferenceEventStream::connect(
            self.inner
                .require_aeron_endpoint()
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            capacity,
        )
    }

    pub fn watermark(&self) -> ContractResult<ReferenceWatermark> {
        ReferenceSqliteReader::open(&self.database)?.watermark()
    }

    pub fn status(&self) -> ContractResult<ReferenceCatalogStatus> {
        ReferenceSqliteReader::open(&self.database)?.status()
    }

    pub fn require_market(
        &self,
        market_id: &kairos_primitives::reference::MarketId,
    ) -> ContractResult<ReferenceMarket> {
        ReferenceSqliteReader::open(&self.database)?
            .market(market_id)?
            .ok_or_else(|| {
                ContractError::Invalid(format!("required Reference market is missing: {market_id}"))
            })
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

pub fn read_market_snapshot(
    database: impl AsRef<std::path::Path>,
    actor_id: &ActorId,
) -> ContractResult<ReferenceProjectionSnapshot> {
    ReferenceSqliteReader::open(database)?.market_snapshot(actor_id.as_str())
}

pub fn read_execution_snapshot(
    database: impl AsRef<std::path::Path>,
    actor_id: &ActorId,
) -> ContractResult<ReferenceProjectionSnapshot> {
    ReferenceSqliteReader::open(database)?.execution_snapshot(actor_id.as_str())
}

pub fn read_account_snapshot(
    database: impl AsRef<std::path::Path>,
    actor_id: &ActorId,
) -> ContractResult<ReferenceProjectionSnapshot> {
    ReferenceSqliteReader::open(database)?.account_snapshot(actor_id.as_str())
}
