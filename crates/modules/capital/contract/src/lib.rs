//! Stable cross-process Capital contract.
//!
//! JSON types in [`control`] are restricted to the explicit command/control
//! boundary. Capital snapshots and events must be added as contract-owned
//! FlatBuffers schemas rather than serialized through these control models.

extern crate self as kairos_capital_contract;

use std::path::Path;

pub mod control;
mod current;
mod error;
pub mod event;
pub mod view;

pub use error::{ContractError, ContractResult};
pub use event::{
    CapitalAeronEventPublisher, CapitalEvent, CapitalEventFrame, CapitalEventStream,
    DecodedCapitalEvent, FlatbuffersCapitalEventWriter, QueuedCapitalEventPublisher,
};
pub type CapitalConnection = kairos_protocol::ContractClient;
pub use kairos_transport::AeronEndpoint;
use kairos_transport::SnapshotEnvelopeMetadata;
pub const CAPITAL_EVENTS_STREAM_ID: i32 = kairos_transport::stream_ids::CAPITAL_EVENTS;
pub const DEFAULT_AERON_CHANNEL: &str = kairos_transport::DEFAULT_CHANNEL;
pub use control::{
    CancelFundingObjectiveRequest, CapitalAvailabilityResponse, CapitalControlError,
    CapitalControlResponse, CapitalControlRpcClient, CapitalControlRpcServer,
    CapitalDemandResponse, CapitalDemandStatus, CapitalHealthResponse, CapitalPlanReconcileStatus,
    CapitalReadinessStatus, FundingLocation, FundingObjectivePriority, FundingObjectiveStatus,
    ObserveCapitalDemandRequest, PublishFundingObjectiveRequest, QueryCapitalAvailabilityRequest,
    ReconcileCapitalPlanRequest, ReconcileCapitalPlanResponse,
};
pub use current::{
    CapitalAlert, CapitalAlertKind, CapitalAlertSeverity, CapitalAvailability, CapitalCurrentView,
    CapitalDemand, CapitalDemandLifecycleStatus, CapitalEarnHolding, CapitalFacts,
    CapitalFundingHorizon, CapitalOperation, CapitalOperationKind, CapitalOperationStatus,
    CapitalPlan, CapitalPlanStatus, CapitalPolicy, CapitalReadiness, CapitalRecoveryAction,
    CapitalReservation, CapitalReservationStatus, CapitalRoute, CapitalRouteKind,
    CapitalSettlementClass, FundingObjective, FundingObjectiveLifecycleStatus, FundingPriority,
};
pub use view::{
    CapitalViewFrame, CapitalViewKey, CapitalViewPublisher, FlatbuffersCapitalViewWriter,
    MmapCapitalViewPublisher, capital_view_path,
};

/// Unified public entry point for the Capital contract.
#[derive(Clone)]
pub struct CapitalClient {
    inner: kairos_protocol::ContractClient,
}

impl CapitalClient {
    pub fn connect(connection: CapitalConnection) -> Self {
        Self { inner: connection }
    }

    pub fn control(&self) -> impl CapitalControlRpcClient + '_ {
        self.inner.control()
    }

    pub fn events(&self, capacity: usize) -> ContractResult<CapitalEventStream> {
        CapitalEventStream::connect(
            self.inner
                .require_aeron_endpoint()
                .map_err(|error| ContractError::Transport(error.to_string()))?,
            capacity,
        )
    }

    pub fn current(&self, capital_group_id: impl Into<String>) -> ContractResult<CapitalCurrent> {
        CapitalCurrent::open(
            self.require_view_root()?,
            CapitalViewKey::current(capital_group_id),
        )
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}

pub struct CapitalCurrent {
    reader: view::CapitalViewReader,
}

impl CapitalCurrent {
    fn open(root: &Path, key: CapitalViewKey) -> ContractResult<Self> {
        Ok(Self {
            reader: view::CapitalViewReader::open(root, key)?,
        })
    }

    pub fn read(&self) -> ContractResult<CapitalCurrentSnapshot> {
        Ok(CapitalCurrentSnapshot {
            frame: self.reader.read()?,
        })
    }

    pub fn key(&self) -> &CapitalViewKey {
        self.reader.key()
    }
}

pub struct CapitalCurrentSnapshot {
    frame: CapitalViewFrame,
}

impl CapitalCurrentSnapshot {
    pub fn envelope_metadata(&self) -> SnapshotEnvelopeMetadata {
        self.frame.envelope_metadata()
    }

    pub fn view(
        &self,
    ) -> ContractResult<kairos_protocol::generated::kairos::capital::v_2::CapitalCurrentView<'_>>
    {
        self.frame.decode()
    }
}
