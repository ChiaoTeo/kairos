//! Strategy-scoped capital management boundary.
//!
//! [`CapitalApplication`] is the public use-case facade. The private Capital
//! Actor is the sole mutable owner of funding objectives and later placement
//! plans; Account remains the owner of physical balances.

pub mod application;
pub mod composition;
pub mod domain;
mod services;

pub use application::{
    AuthorizeCapitalPlan, BeginCapitalOperation, CancelFundingObjective, CapitalApplication,
    CapitalDemandReceipt, CapitalError, CapitalEvent, CapitalProcessError, CapitalSnapshot,
    CapitalTransferProcess, EvaluateCapitalGroup, ExpireCapitalDemands, ExpireCapitalPlans,
    ExpireFundingObjectives, FundingObjectiveReceipt, MarkCapitalDeliveryStarted,
    ObserveCapitalDemand, ObserveCapitalFacts, ObserveCapitalSettlement, PublishFundingObjective,
    RecordCapitalParticipantStatus, RecordCapitalSubmission, UpdateCapitalPolicy,
    UpdateCapitalRoute,
};
pub use domain::{
    CapitalAvailabilityView, CapitalDemand, CapitalDemandId, CapitalDemandRecord,
    CapitalDemandStatus, CapitalFacts, CapitalGroupConfig, CapitalGroupId, CapitalGroupMember,
    CapitalOperation, CapitalOperationId, CapitalOperationStatus, CapitalParticipantOperationState,
    CapitalPlan, CapitalPlanId, CapitalPlanStatus, CapitalPolicy, CapitalReadiness,
    CapitalReservation, CapitalReservationId, CapitalReservationStatus, CapitalRouteId,
    CapitalRouteKind, CapitalSettlementClass, CapitalSubmissionOutcome, CapitalTransferRoute,
    FundingLocation, FundingObjective, FundingObjectiveId, FundingObjectiveRecord,
    FundingObjectiveStatus, FundingPriority,
};
