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
    AuthorizeCapitalPlan, AuthorizeEarnSubscriptionPlan, BeginCapitalOperation,
    CancelFundingObjective, CapitalApplication, CapitalDemandReceipt, CapitalError, CapitalEvent,
    CapitalProcessError, CapitalSnapshot, CapitalTransferProcess, CapitalYieldCandidate,
    EvaluateCapitalGroup, ExpireCapitalDemands, ExpireCapitalPlans, ExpireFundingObjectives,
    FundingObjectiveReceipt, MarkCapitalDeliveryStarted, ObserveCapitalDemand, ObserveCapitalFacts,
    ObserveCapitalMemberAccount, ObserveCapitalSettlement, PublishFundingObjective,
    RecordCapitalParticipantStatus, RecordCapitalRecoveryRequired, RecordCapitalSubmission,
    UpdateCapitalPolicy, UpdateCapitalRoute,
};
pub use domain::{
    CapitalAvailabilityView, CapitalDemand, CapitalDemandId, CapitalDemandRecord,
    CapitalDemandStatus, CapitalEarnHoldingFact, CapitalFacts, CapitalFundingHorizon,
    CapitalGroupConfig, CapitalGroupId, CapitalGroupMember, CapitalMemberAccountObservation,
    CapitalMemberReadinessRole, CapitalOperation, CapitalOperationId, CapitalOperationKind,
    CapitalOperationStatus, CapitalParticipantOperationState, CapitalPlan, CapitalPlanId,
    CapitalPlanStatus, CapitalPolicy, CapitalReadiness, CapitalRecoveryAction, CapitalReservation,
    CapitalReservationId, CapitalReservationStatus, CapitalRouteId, CapitalRouteKind,
    CapitalSettlementClass, CapitalSubmissionOutcome, CapitalTransferRoute, FundingLocation,
    FundingObjective, FundingObjectiveId, FundingObjectiveRecord, FundingObjectiveStatus,
    FundingPriority,
};
