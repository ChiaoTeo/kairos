//! Stable cross-process Capital contract.
//!
//! JSON types in [`control`] are restricted to the explicit command/control
//! boundary. Capital snapshots and events must be added as contract-owned
//! FlatBuffers schemas rather than serialized through these control models.

pub mod control;
mod error;
pub mod event;
mod projection;
pub mod view;

pub use error::{ContractError, ContractResult};
pub use event::{
    CapitalAeronEventPublisher, CapitalEvent, CapitalEventFrame, CapitalEventStream,
    DecodedCapitalEvent, FlatbuffersCapitalEventWriter, QueuedCapitalEventPublisher,
};
pub use kairos_transport::AeronEndpoint;
pub const CAPITAL_EVENTS_STREAM_ID: i32 = kairos_transport::stream_ids::CAPITAL_EVENTS;
pub const DEFAULT_AERON_CHANNEL: &str = kairos_transport::DEFAULT_CHANNEL;
pub use control::{
    CancelFundingObjectiveRequest, CapitalAvailabilityResponse, CapitalControlError,
    CapitalControlResponse, CapitalDemandResponse, CapitalDemandStatus, CapitalPlanReconcileStatus,
    CapitalReadinessStatus, FundingLocation, FundingObjectivePriority, FundingObjectiveStatus,
    ObserveCapitalDemandRequest, PublishFundingObjectiveRequest, QueryCapitalAvailabilityRequest,
    ReconcileCapitalPlanRequest, ReconcileCapitalPlanResponse,
};
pub use projection::{
    CapitalAlert, CapitalAlertKind, CapitalAlertSeverity, CapitalAvailability, CapitalCurrentView,
    CapitalDemand, CapitalDemandLifecycleStatus, CapitalEarnHolding, CapitalFacts,
    CapitalFundingHorizon, CapitalOperation, CapitalOperationKind, CapitalOperationStatus,
    CapitalPlan, CapitalPlanStatus, CapitalPolicy, CapitalReadiness, CapitalRecoveryAction,
    CapitalReservation, CapitalReservationStatus, CapitalRoute, CapitalRouteKind,
    CapitalSettlementClass, FundingObjective, FundingObjectiveLifecycleStatus, FundingPriority,
};
pub use view::{
    CapitalViewFrame, CapitalViewKey, CapitalViewPublisher, CapitalViewReader,
    FlatbuffersCapitalViewWriter, MmapCapitalViewPublisher, capital_view_path,
};
