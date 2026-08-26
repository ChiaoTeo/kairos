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
    CapitalAlert, CapitalAlertKind, CapitalAlertSeverity, CapitalAvailability,
    CapitalCurrentRecords, CapitalDemand, CapitalDemandLifecycleStatus, CapitalEarnHolding,
    CapitalFacts, CapitalFundingHorizon, CapitalOperation, CapitalOperationKind,
    CapitalOperationStatus, CapitalPlan, CapitalPlanStatus, CapitalPolicy, CapitalReadiness,
    CapitalRecoveryAction, CapitalReservation, CapitalReservationStatus, CapitalRoute,
    CapitalRouteKind, CapitalSettlementClass, FundingObjective, FundingObjectiveLifecycleStatus,
    FundingPriority,
};
pub use view::{
    CAPITAL_ALERTS_DATABASE, CAPITAL_AVAILABILITY_DATABASE, CAPITAL_DEMANDS_DATABASE,
    CAPITAL_FACTS_DATABASE, CAPITAL_MAP_SIZE, CAPITAL_OBJECTIVES_DATABASE,
    CAPITAL_OPERATIONS_DATABASE, CAPITAL_PLANS_DATABASE, CAPITAL_POLICIES_DATABASE,
    CAPITAL_RESERVATIONS_DATABASE, CAPITAL_RESOURCE_EPOCH, CAPITAL_ROUTES_DATABASE,
    CAPITAL_STATE_DATABASE, CapitalIndexedEntity, CapitalIndexedSnapshot, CapitalIndexedView,
    capital_indexed_environment_path, capital_indexed_identity, capital_indexed_key,
    capital_indexed_schema_set, encode_indexed_current, location_key,
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

    pub fn indexed_current(
        &self,
        identity: &kairos_primitives::runtime::InstanceIdentity,
        capital_group_id: kairos_primitives::capital::CapitalGroupId,
    ) -> ContractResult<CapitalIndexedView> {
        CapitalIndexedView::open(self.require_view_root()?, identity, capital_group_id)
    }

    fn require_view_root(&self) -> ContractResult<&Path> {
        self.inner
            .require_view_root()
            .map_err(|error| ContractError::Transport(error.to_string()))
    }
}
