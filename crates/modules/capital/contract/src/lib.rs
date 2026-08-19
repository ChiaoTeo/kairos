//! Stable cross-process Capital contract.
//!
//! JSON types in [`control`] are restricted to the explicit command/control
//! boundary. Capital snapshots and events must be added as contract-owned
//! FlatBuffers schemas rather than serialized through these control models.

pub mod control;

pub use control::{
    CancelFundingObjectiveRequest, CapitalAvailabilityResponse, CapitalControlError,
    CapitalControlResponse, CapitalDemandResponse, CapitalDemandStatus, CapitalReadinessStatus,
    FundingLocation, FundingObjectivePriority, FundingObjectiveStatus, ObserveCapitalDemandRequest,
    PublishFundingObjectiveRequest, QueryCapitalAvailabilityRequest,
};
