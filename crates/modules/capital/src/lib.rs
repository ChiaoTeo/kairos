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
    CancelFundingObjective, CapitalApplication, CapitalCliRequestKind, CapitalConfluxConfig,
    CapitalDemandReceipt, CapitalError, CapitalEvent, CapitalProcess, CapitalProcessError,
    CapitalSnapshot, CapitalStandaloneOutput, CapitalYieldCandidate, CliCapitalApplication,
    CliCapitalTransferApplication, ConfirmManualCapitalTransfer, ConnectedCapitalApplication,
    ConnectedCapitalOutput, EvaluateCapitalGroup, ExpireCapitalDemands, ExpireCapitalPlans,
    ExpireFundingObjectives, FundingObjectiveReceipt, ManualCapitalTransferPreview,
    MarkCapitalDeliveryStarted, ObserveCapitalDemand, ObserveCapitalFacts,
    ObserveCapitalMemberAccount, ObserveCapitalSettlement, PreviewManualCapitalTransfer,
    PublishFundingObjective, RecordCapitalParticipantStatus, RecordCapitalRecoveryRequired,
    RecordCapitalSubmission, StandaloneCapitalOperationResult, StandaloneCapitalPlanResult,
    StandaloneCapitalSegmentBinding, StandaloneCapitalTransferBinding,
    StandaloneCapitalTransferHistoryItem, StandaloneCapitalTransferHistoryResult,
    StandaloneCapitalTransferPreviewRequest, StandaloneCapitalTransferPreviewResult,
    StandaloneCapitalTransferResult, UpdateCapitalPolicy, UpdateCapitalRoute,
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
